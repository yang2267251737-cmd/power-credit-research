import copy
import unittest
from decimal import Decimal
from power_credit.model import normalize, derive, stress
from power_credit.build import ROOT, read_csv, validate

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
        self.assertEqual(len(groups),6)
        hpi=groups[('HPI','2025-12-31')]
        self.assertEqual(derive(hpi)['ocf_after_cash_capex'],Decimal('8886576.608'))
        self.assertTrue(all(f['basis']=='restated_comparative' for f in self.facts if f['issuer_id']=='HDPI' and f['period_end']=='2024-12-31'))

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
