"""Terminal access to the same offline help returned by mf.help()."""
import argparse
import json
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python -m mipframe')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('help', help='Search installed offline documentation')
    p.add_argument('topic', nargs='?', default=None)
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--detail', choices=('brief', 'full'), default='brief')
    p.add_argument('--format', choices=('text', 'json'), default='text')
    args = parser.parse_args(argv)
    from mipframe._help.query import query
    from mipframe._help.render import render
    try:
        result = query(args.topic, limit=args.limit, detail=args.detail, format=args.format)
        print(json.dumps(result, indent=2, ensure_ascii=False) if args.format == 'json' else render(result))
        return 0 if result['results'] else 1
    except (ValueError, TypeError, OSError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
