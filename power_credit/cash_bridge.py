"""Conditional Huaneng parent-company cash bridge, never a forced reconciliation."""
from decimal import Decimal
from .model import normalize

KEY_FIELDS = ['issuer_id', 'period_end', 'scope', 'report_vintage']
CASE = ('HPI', '2025-12-31', 'parent', '2025')
INPUT_BASIS = {
    'dividends_receivable_opening': 'opening_balance',
    'dividends_receivable_closing': 'closing_balance',
    'equity_method_dividends_declared': 'declared_distribution',
}

def derive_cash_bridge(inputs, receivables, income, sources):
    source_map = {s['source_id']: s for s in sources}
    for row in inputs + receivables:
        if tuple(row[k] for k in KEY_FIELDS) != CASE:
            raise ValueError('Cash bridge supports this Huaneng parent case only')
        source = source_map.get(row['source_id'])
        if source is None or source['issuer_id'] != 'HPI' or row['source_id'] != 'HPI_2025_AR':
            raise ValueError('Cash bridge source mismatch')
        if row['unit'] != 'CNY' or row['currency'] != 'CNY' or row['review_status'] != 'agent_verified':
            raise ValueError('Cash bridge inputs require original CNY evidence and AI review')
        if min(int(row['pdf_page']), int(row['report_page'])) < 1:
            raise ValueError('Missing cash bridge evidence page')
    index = {r['component']: r for r in inputs}
    if len(index) != len(inputs) or set(index) != set(INPUT_BASIS):
        raise ValueError('Missing or duplicate cash bridge input')
    for key, row in index.items():
        if row['measurement_basis'] != INPUT_BASIS[key] or Decimal(row['value']) < 0:
            raise ValueError('Cash bridge basis/value mismatch')
    detail_index = {}
    for row in receivables:
        key = (row['table_kind'], row['counterparty'])
        if key in detail_index or row['table_kind'] not in ['receivables', 'important_over_one_year']:
            raise ValueError('Duplicate or unknown receivables row')
        detail_index[key] = row
        for field in ['opening', 'closing']:
            if row[field] and Decimal(row[field]) < 0:
                raise ValueError('Negative dividends receivable')
    total = detail_index.get(('receivables', '合计'))
    if total is None:
        raise ValueError('Missing dividends receivable total')
    for field, component in [('opening', 'dividends_receivable_opening'), ('closing', 'dividends_receivable_closing')]:
        # Add disclosed numbers; this is not an assertion that blank balances are zero.
        disclosed_sum = sum((Decimal(r[field]) for r in receivables
            if r['table_kind'] == 'receivables' and r['counterparty'] != '合计' and r[field]), Decimal(0))
        if disclosed_sum != Decimal(total[field]) or total[field] != index[component]['value']:
            raise ValueError('Dividends receivable detail/total/input mismatch')
    aged = [r for r in receivables if r['table_kind'] == 'important_over_one_year']
    if not aged:
        raise ValueError('Missing important aged receivables evidence')
    for row in aged:
        original = detail_index.get(('receivables', row['counterparty']))
        if original is None or any(row[f] != original[f] for f in ['opening', 'closing']):
            raise ValueError('Aged receivables do not match main table')
    income_index = {r['component']: r for r in income if tuple(r[k] for k in KEY_FIELDS) == CASE}
    required = ['cost_method_income', 'entrusted_loan_income', 'investment_income_cash']
    if any(k not in income_index for k in required):
        raise ValueError('Missing cash bridge income evidence')
    amount = lambda row: normalize(row['value'], row['unit'])
    opening = amount(index['dividends_receivable_opening'])
    closing = amount(index['dividends_receivable_closing'])
    declared = amount(index['equity_method_dividends_declared'])
    cost, loan, actual = (amount(income_index[k]) for k in required)
    dividend_proxy = cost + declared + opening - closing
    conditional = dividend_proxy + loan
    aged_closing = sum((normalize(r['closing'], r['unit']) for r in aged if r['closing']), Decimal(0))
    if aged_closing > closing:
        raise ValueError('Aged receivables exceed closing balance')
    values = {
        'cost_method_income': cost, 'equity_method_dividends_declared': declared,
        'dividends_receivable_opening': opening, 'dividends_receivable_closing': closing,
        'receivables_decrease': opening-closing,
        'conditional_dividend_receipts': dividend_proxy,
        'entrusted_loan_income': loan, 'conditional_investment_receipts': conditional,
        'actual_investment_receipts': actual, 'actual_minus_conditional': actual-conditional,
        'important_aged_disclosed_closing': aged_closing,
    }
    return {**dict(zip(KEY_FIELDS, CASE)), 'unit': 'CNY_1000',
        'amounts': {k: str(v) for k, v in values.items()},
        'important_aged_disclosed_share': str(aged_closing/closing) if closing else None,
        'status': 'partial_unreconciled', 'interpretation': 'conditional_scenario_only',
        'assumptions': [
            '成本法收益暂作本期宣告股利代理，完整被投资单位及股利来源尚未桥接',
            '应收股利总额暂假设仅由宣告与收现变动，未排除合并范围、外币、抵销或其他非现金影响',
            '委托贷款收益暂作利息收现代理，未核实实际到账及应收利息变动',
        ],
        'limitations': [
            '联合营宣告分红不等于实际到账；没有以权益法利润份额代替分红',
            '应收股利净减少不等于已确认的跨期现金收回金额',
            '原表空白保留；重要超1年项目的已列金额不代表全部超1年应收股利',
            '即使残差为零，也不能仅凭数值吻合把假设标为已验证现金来源',
        ]}
