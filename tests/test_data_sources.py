import unittest
from unittest.mock import patch

import data_sources


class DataSourceModuleTest(unittest.TestCase):
    def test_lists_selectable_source_types(self):
        types = data_sources.list_source_types()
        self.assertEqual([item["type"] for item in types], ["mysql", "postgresql", "sqlserver", "api", "webhook"])

    def test_mysql_url_requires_connection_fields(self):
        with self.assertRaises(ValueError):
            data_sources._connection_url({"type": "mysql"})

    def test_mysql_url_is_built_without_connecting(self):
        url, source_type = data_sources._connection_url({
            "type": "mysql", "host": "127.0.0.1", "port": 3306,
            "database": "demo", "username": "user", "password": "p@ss",
        })
        self.assertEqual(source_type, "mysql")
        self.assertIn("mysql+pymysql", url.render_as_string(hide_password=False))
        self.assertIn("p%40ss", url.render_as_string(hide_password=False))

    def test_remote_source_rejects_non_http_urls(self):
        with self.assertRaises(ValueError):
            data_sources.fetch_remote_dataset({"url": "file:///tmp/data.csv"})

    def test_remote_json_is_normalized_to_csv(self):
        class FakeResponse:
            headers = {"Content-Type": "application/json"}

            def read(self, _limit):
                return b'{"items":[{"region":"East","amount":10}]}'

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch.object(data_sources, "urlopen", return_value=FakeResponse()):
            filename, content, source_type = data_sources.fetch_remote_dataset({"url": "https://example.test/data", "name": "sales"})
        self.assertEqual((filename, source_type), ("sales.csv", "api"))
        self.assertIn("region,amount", content.decode("utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
