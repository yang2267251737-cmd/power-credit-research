import copy
import unittest
from decimal import Decimal
from power_credit.model import normalize, derive, stress
from power_credit.build import ROOT, read_csv, validate, reconcile_versions, validate_debt_breakdown

class FinancialTests(unittest.TestCase):
    def test_equal_currency_amounts_produce_identical_results(self):
        amounts=[normalize('100000000','CNY'),normalize('100000','CNY_1000'),normalize('1','CNY_100M')]
        self.assertEqual(amounts,[Decimal('100000')]*3)

    def test_missing_debt_remains_unknown_instead_of_zero(self):
        values={'monetary_funds':Decimal('12'),'restricted_monetary_funds':Decimal('2')}
        self.assertIsNone(derive(values)['adjusted_cash_short_debt'])
        values['short_term_interest_bearing_debt']=Decimal('0')
        self.assertIsNone(derive(values)['adjusted_cash_short_debt'])

    def test_negative_cash_surplus_is_preserved(self):
        values={'operating_cash_flow':Decimal('100'),'cash_capex':Decimal('110')}
        self.assertEqual(derive(values)['ocf_after_cash_capex'],Decimal('-10'))
        self.assertEqual(stress(values),Decimal('-41'))

    def test_reclassification_does_not_change_reported_leverage(self):
        values={'total_assets':Decimal('100'),'total_liabilities':Decimal('60'),'perpetual_equity':Decimal('20')}
        result=derive(values)
        self.assertEqual(result['liabilities_assets'],Decimal('.6'))
        self.assertEqual(result['perpetual_reclass_leverage'],Decimal('.8'))

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.issuers=read_csv(ROOT/'data/issuers.csv')
        self.sources=read_csv(ROOT/'data/sources.csv')
        self.facts=read_csv(ROOT/'data/facts.csv')

    def test_dataset_has_traceable_valid_facts(self):
        groups=validate(self.issuers,self.sources,self.facts)
        self.assertEqual(len(groups),24)
        hpi=groups[('HPI','2025-12-31','consolidated','2025')]
        self.assertEqual(derive(hpi)['ocf_after_cash_capex'],Decimal('8886576.608'))
        self.assertTrue(all(f['basis']=='restated_comparative' for f in self.facts if f['issuer_id']=='HDPI' and f['period_end']=='2024-12-31' and f['report_vintage']=='2025' and f['scope']=='consolidated' and f['metric']!='debt_disclosed_opening'))

    def test_parent_and_consolidated_cash_do_not_overwrite_each_other(self):
        groups=validate(self.issuers,self.sources,self.facts)
        self.assertEqual(groups[('HPI','2025-12-31','parent','2025')]['monetary_funds'],Decimal('582228.150'))
        self.assertEqual(groups[('HPI','2025-12-31','consolidated','2025')]['monetary_funds'],Decimal('19455512.962'))

    def test_restatement_is_preserved_and_debt_opening_is_not_a_denominator(self):
        groups=validate(self.issuers,self.sources,self.facts)
        old=groups[('HDPI','2024-12-31','consolidated','2024')]
        new=groups[('HDPI','2024-12-31','consolidated','2025')]
        self.assertEqual(old['total_assets'],Decimal('223875372'))
        self.assertEqual(new['total_assets'],Decimal('265401631'))
        self.assertIsNone(derive(new)['ocf_debt'])
        change=next(r for r in reconcile_versions(self.facts) if r['issuer_id']=='HDPI' and r['scope']=='consolidated' and r['metric']=='interest_bearing_debt')
        self.assertEqual(Decimal(change['delta_cny_1000']),Decimal('24492000'))
        self.assertEqual(change['comparison_type'],'year_end_vs_next_report_opening')

    def test_negative_parent_ocf_is_preserved(self):
        groups=validate(self.issuers,self.sources,self.facts)
        values=groups[('DT','2023-12-31','parent','2024')]
        self.assertEqual(values['operating_cash_flow'],Decimal('-855114'))
        with self.assertRaisesRegex(ValueError,'non-negative'):
            stress(values)

    def test_debt_categories_reconcile_and_corruption_is_rejected(self):
        rows=read_csv(ROOT/'data/debt_breakdown.csv')
        exceptions=read_csv(ROOT/'data/evidence_issues.csv')
        validate_debt_breakdown(rows,self.facts,exceptions)
        rows[0]['total']='9999'
        with self.assertRaisesRegex(ValueError,'rounding tolerance'):
            validate_debt_breakdown(rows,self.facts,exceptions)

    def test_source_contradiction_requires_an_exact_registered_exception(self):
        rows=read_csv(ROOT/'data/debt_breakdown.csv')
        with self.assertRaisesRegex(ValueError,'rounding tolerance'):
            validate_debt_breakdown(rows,self.facts)
        exceptions=read_csv(ROOT/'data/evidence_issues.csv')
        exceptions[0]['expected_delta']='0.80'
        with self.assertRaisesRegex(ValueError,'no longer matches'):
            validate_debt_breakdown(rows,self.facts,exceptions)

    def test_vintage_cannot_be_changed_without_changing_evidence_source(self):
        facts=copy.deepcopy(self.facts)
        facts[0]['report_vintage']='2026'
        with self.assertRaisesRegex(ValueError,'vintage/source mismatch'):
            validate(self.issuers,self.sources,facts)

    def test_duplicate_fact_is_rejected(self):
        with self.assertRaisesRegex(ValueError,'Duplicate fact'):
            validate(self.issuers,self.sources,self.facts+[self.facts[0]])

    def test_another_issuer_source_is_rejected(self):
        facts=copy.deepcopy(self.facts)
        facts[0]['source_id']='HDPI_2025_AR'
        with self.assertRaisesRegex(ValueError,'issuer mismatch'):
            validate(self.issuers,self.sources,facts)

    def test_restricted_cash_cannot_exceed_total_cash(self):
        facts=copy.deepcopy(self.facts)
        fact=next(f for f in facts if f['issuer_id']=='HPI' and f['metric']=='restricted_monetary_funds' and f['period_end']=='2025-12-31')
        fact['value']='999999999999'
        with self.assertRaisesRegex(ValueError,'Inconsistent balances'):
            validate(self.issuers,self.sources,facts)

if __name__=='__main__':
    unittest.main()
