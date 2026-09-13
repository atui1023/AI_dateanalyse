import unittest

import kb


class EmbeddingConfigTest(unittest.TestCase):
    def test_legacy_remote_collection_is_preserved(self):
        self.assertEqual(
            kb.embedding_collection_name("remote", "text-embedding-v3"),
            "kb_store",
        )

    def test_different_models_get_isolated_collections(self):
        first = kb.embedding_collection_name("remote", "model-a")
        second = kb.embedding_collection_name("remote", "model-b")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("kb_store_remote_"))

    def test_local_collection_does_not_depend_on_api_key(self):
        name = kb.embedding_collection_name("local", "BAAI/bge-small-zh-v1.5")
        self.assertTrue(name.startswith("kb_store_local_"))


if __name__ == "__main__":
    unittest.main()
