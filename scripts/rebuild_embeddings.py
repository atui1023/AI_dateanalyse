"""用当前 .env 中的向量模型重建全部知识库向量。"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import kb


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild knowledge-base embeddings")
    parser.add_argument("--user-id", type=int, default=None, help="只重建指定用户")
    args = parser.parse_args()
    result = kb.rebuild_embeddings(user_id=args.user_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
