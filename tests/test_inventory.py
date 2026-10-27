import unittest

from app.inventory import canonical, choose_pages, choose_products, coverage, ingest, inventory_value, link_kind, mark


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.inventory=inventory_value([])
        for group in range(4):
            ingest(self.inventory,
                   [f'https://soluparts.cl/Product/Details.rails?id={group*10+i}' for i in range(5)],
                   f'https://soluparts.cl/Product/ListByCategory.rails?categoryId={group}',
                   '2026-10-27T19:00:00+00:00')

    def test_no_repeats_while_unvisited_products_remain(self):
        first=choose_products(self.inventory,12,1)
        for url in first:
            mark(self.inventory,url,'product','2026-10-27T19:00:00+00:00','ok')
        second=choose_products(self.inventory,8,2)
        self.assertEqual(len(set(first+second)),20)
        self.assertEqual(coverage(self.inventory)['unique_products_successfully_loaded'],12)
        self.assertEqual(coverage(self.inventory)['products_not_yet_checked'],8)

    def test_categories_are_balanced_within_a_cycle(self):
        selection=choose_products(self.inventory,12,1)
        groups=[self.inventory['products'][url]['group'] for url in selection]
        self.assertEqual(len(set(groups)),4)
        self.assertTrue(all(groups.count(group)==3 for group in set(groups)))

    def test_oldest_products_return_after_all_have_been_attempted(self):
        for i,url in enumerate(sorted(self.inventory['products'])):
            mark(self.inventory,url,'product',f'2026-10-27T19:{i:02}:00+00:00','ok')
        selection=choose_products(self.inventory,4,2)
        for url in selection:
            group=self.inventory['products'][url]['group']
            pool=[x for x in self.inventory['products'].values() if x['group']==group]
            self.assertEqual(url,min(pool,key=lambda x:x['last_checked'])['url'])

    def test_deduplicates_tracking_urls_and_excludes_checkout_and_external_links(self):
        self.assertEqual(canonical('http://www.soluparts.cl/Product/Details.rails?utm_source=x&id=4#image'),
                         'https://soluparts.cl/Product/Details.rails?id=4')
        self.assertIsNone(canonical('https://example.com/product/4'))
        self.assertIsNone(link_kind('https://soluparts.cl/account/Product/Details.rails?id=4'))
        self.assertEqual(link_kind('https://soluparts.cl/Product/ListByCategory.rails?categoryId=4'),'listing')

    def test_discovery_keeps_unvisited_pages_ahead_of_repeated_pages(self):
        links=[{'url':f'https://soluparts.cl/Product/ListByCategory.rails?categoryId={i}','kind':'listing'} for i in range(6)]
        ingest(self.inventory,links,'https://soluparts.cl/','2026-10-27T19:00:00+00:00')
        first=choose_pages(self.inventory,4)
        for url in first:
            mark(self.inventory,url,'listing','2026-10-27T19:00:00+00:00','ok')
        second=choose_pages(self.inventory,2)
        self.assertTrue(set(first).isdisjoint(second))

    def test_discovery_cap_is_explicit_in_coverage(self):
        ingest(self.inventory,['https://soluparts.cl/Product/Details.rails?id=999'],
               'https://soluparts.cl/','2026-10-27T19:00:00+00:00',product_limit=20)
        self.assertEqual(coverage(self.inventory)['products_discovered'],20)
        self.assertTrue(coverage(self.inventory)['discovery_limited'])


if __name__=='__main__':
    unittest.main()
