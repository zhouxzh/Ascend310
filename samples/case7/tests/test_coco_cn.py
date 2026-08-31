import unittest

from scripts.prepare_coco_cn import build_queries


class CocoCnFixtureTests(unittest.TestCase):
    def test_query_contract_has_twenty_bilingual_queries(self):
        records = []
        concepts = [
            ("dog", "狗"), ("cat", "猫"), ("horse", "马"),
            ("bicycle", "自行车"), ("car", "汽车"), ("bus", "公交车"),
            ("train", "火车"), ("airplane", "飞机"), ("street", "街道"),
            ("kitchen", "厨房"), ("beach", "海滩"), ("park", "公园"),
            ("snow", "雪"), ("pizza", "披萨"), ("cake", "蛋糕"),
            ("baseball", "棒球"), ("laptop", "电脑"), ("chair", "椅子"),
            ("table", "桌子"), ("boat", "船"),
        ]
        for index, (english, chinese) in enumerate(concepts):
            for repeat in range(3):
                records.append(
                    {
                        "image_id": f"image-{index}-{repeat}",
                        "caption_en": [f"a {english} in a scene"],
                        "caption_zh": [f"一张{chinese}图片"],
                        "tags_zh": [chinese],
                    }
                )
        queries = build_queries(records)
        self.assertEqual(len(queries["en"]), 20)
        self.assertEqual(len(queries["zh"]), 20)
        self.assertTrue(all(len(item["relevant_image_ids"]) >= 3 for item in queries["en"]))
        self.assertTrue(all(len(item["relevant_image_ids"]) >= 3 for item in queries["zh"]))


if __name__ == "__main__":
    unittest.main()
