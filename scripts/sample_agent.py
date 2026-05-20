#!/usr/bin/env python3
import json
import sys


def main():
    prompt = sys.stdin.read().strip()
    if not prompt:
        prompt = "empty prompt"
    print(json.dumps({"reply": f"sample agent reply: {prompt[:200]}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
