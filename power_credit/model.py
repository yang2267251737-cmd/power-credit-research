"""All amounts are normalized to CNY thousands using Decimal."""
from decimal import Decimal

SCALES = {'CNY': Decimal('0.001'), 'CNY_1000': Decimal('1'),
          'CNY_100M': Decimal('100000')}
METRICS = {
    'total_assets': '总资产',
    'total_liabilities': '总负债',
    'current_assets': '流动资产',
    'current_liabilities': '流动负债',
    'monetary_funds': '货币资金',
    'restricted_monetary_funds': '披露的受限货币资金',
    'remittance_restricted_funds': '境外汇回受限资金（另列）',
    'operating_cash_flow': '经营活动现金流净额',
    'cash_capex': '购建长期资产支付的现金',
    'interest_bearing_debt': '披露的合并有息债务',
    'short_term_interest_bearing_debt': '一年以内到期有息债务',
    'perpetual_equity': '权益列报永续债',
}
DERIVED = {
    'liabilities_assets': ('资产负债率', 'percent'),
    'current_ratio': ('流动比率', 'multiple'),
    'cash_short_debt': ('货币资金 / 短期有息债务', 'multiple'),
    'adjusted_cash_short_debt': ('扣披露受限资金后现金 / 短债（代理）', 'multiple'),
    'ocf_debt': ('经营现金流 / 有息债务', 'percent'),
    'short_debt_share': ('一年内到期债务占比', 'percent'),
    'ocf_after_cash_capex': ('经营现金流减购建支出（亿元）', 'amount'),
    'ocf_cash_capex': ('经营现金流 / 购建支出', 'multiple'),
    'perpetual_reclass_leverage': ('永续债100%重分类情景杠杆率', 'percent'),
}

def normalize(value, unit):
    number = Decimal(str(value))
    if not number.is_finite():
        raise ValueError('Amount must be finite')
    return number * SCALES[unit]

def ratio(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return numerator / denominator

def difference(left, right):
    return None if left is None or right is None else left - right

def derive(values):
    get = values.get
    adjusted_cash = difference(get('monetary_funds'), get('restricted_monetary_funds'))
    equity = get('perpetual_equity')
    liabilities = get('total_liabilities')
    reclass = None if liabilities is None or equity is None else liabilities + equity
    return {
        'liabilities_assets': ratio(liabilities, get('total_assets')),
        'current_ratio': ratio(get('current_assets'), get('current_liabilities')),
        'cash_short_debt': ratio(get('monetary_funds'), get('short_term_interest_bearing_debt')),
        'adjusted_cash_short_debt': ratio(adjusted_cash, get('short_term_interest_bearing_debt')),
        'ocf_debt': ratio(get('operating_cash_flow'), get('interest_bearing_debt')),
        'short_debt_share': ratio(get('short_term_interest_bearing_debt'), get('interest_bearing_debt')),
        'ocf_after_cash_capex': difference(get('operating_cash_flow'), get('cash_capex')),
        'ocf_cash_capex': ratio(get('operating_cash_flow'), get('cash_capex')),
        'perpetual_reclass_leverage': ratio(reclass, get('total_assets')),
    }

def stress(values, ocf_decline=Decimal('0.2'), capex_increase=Decimal('0.1')):
    """Hypothetical cash-flow shock; not a debt service forecast or rating."""
    if not Decimal('0') <= ocf_decline <= Decimal('1') or capex_increase < 0:
        raise ValueError('Invalid scenario parameters')
    ocf, capex = values.get('operating_cash_flow'), values.get('cash_capex')
    if ocf is None or capex is None:
        return None
    if ocf < 0:
        raise ValueError('This decline scenario requires non-negative operating cash flow')
    return ocf * (1 - ocf_decline) - capex * (1 + capex_increase)
