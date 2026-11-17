"""Regression from observed public ledger, not a hypothetical fixture schema."""
import unittest
import public_evidence
class LiveSchemaTests(unittest.TestCase):
    def test_observed_kind_amount_schema_is_ingested(self):
        row={'seq':2356229,'kind':'settle','round':3,'data':{'shop':'https://own.example','item':'Ga2e3075f','amount':9400,'order':'o-25e38a495d15'}}
        result=public_evidence.ingest([row],{'Ga2e3075f':{'category':'bedding'}},['https://own.example'])
        self.assertEqual(result['settle_count'],1)
        self.assertEqual(result['own_ledger_ids'],[2356229])
        result=public_evidence.ingest([row],{'Ga2e3075f':{'category':'bedding'}},[])
        self.assertEqual(result['category_counts'],{'bedding':1})
