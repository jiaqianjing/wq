#!/usr/bin/env python3
"""
WorldQuant Brain Alpha 自动提交脚本

使用方法:
  1. 配置环境变量: export WQB_USERNAME="your_username"
                   export WQB_PASSWORD="your_password"
  2. 运行: python main.py [command] [options]

Commands:
  simulate  - 仅模拟 Alpha，不提交
  submit    - 模拟并提交符合条件的 Alpha
  pending   - 提交所有待提交的 Alpha
  report    - 生成历史提交报告

Options:
  --type, -t     - 指定 Alpha 类型: all, atom, regular, power_pool, superalpha
  --count, -c    - 指定生成数量
  --region, -r   - 指定交易区域: USA, CHN, EUR
  --config, -f   - 指定配置文件路径
"""

import os
import sys
import argparse
import logging
from typing import Optional
from pathlib import Path

try:
    import yaml
except ImportError:
    yaml = None

from dotenv import load_dotenv

from wq_brain import WorldQuantBrainClient, AlphaGenerator, AlphaSubmitter
from wq_brain.client import Region, Unviverse
from wq_brain.alpha_submitter import SubmissionCriteria

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('wq_brain.log')
    ]
)
logger = logging.getLogger(__name__)


def load_config(config_path: str = "config.yaml") -> dict:
    """加载配置文件"""
    if yaml and os.path.exists(config_path):
        with open(config_path, 'r') as f:
            return yaml.safe_load(f)
    return {}


def get_auth_credentials() -> tuple:
    """获取认证信息"""
    username = os.getenv('WQB_USERNAME')
    password = os.getenv('WQB_PASSWORD')

    if not username or not password:
        logger.error("请设置环境变量 WQB_USERNAME 和 WQB_PASSWORD")
        logger.error("或使用 .env 文件: cp .env.example .env 并编辑")
        sys.exit(1)

    return username, password


def create_client(username: str, password: str) -> WorldQuantBrainClient:
    """创建 API 客户端"""
    client = WorldQuantBrainClient(username, password)
    if not client.authenticate():
        logger.error("认证失败，请检查用户名和密码")
        sys.exit(1)
    return client


def cmd_simulate(args):
    """模拟 Alpha 命令"""
    username, password = get_auth_credentials()
    client = create_client(username, password)

    generator = AlphaGenerator()

    # 确定要生成的类型
    if args.type == 'all':
        alphas_by_type = generator.generate_all_types(
            regular_count=args.count,
            power_pool_count=args.count,
            atom_count=args.count,
            superalpha_count=args.count // 2
        )
    else:
        type_map = {
            'atom': generator.generate_atoms,
            'regular': generator.generate_regular_alphas,
            'power_pool': generator.generate_power_pool_alphas,
            'superalpha': generator.generate_superalphas
        }
        generator_fn = type_map.get(args.type, generator.generate_regular_alphas)
        alphas_by_type = {args.type: generator_fn(args.count)}

    # 解析区域和股票池
    region = Region(args.region.upper())
    universe = Unviverse.TOP3000

    submitter = AlphaSubmitter(client)

    # 模拟所有 Alpha
    for alpha_type, alphas in alphas_by_type.items():
        logger.info(f"\n模拟 {alpha_type} 类型 Alpha，共 {len(alphas)} 个")

        criteria = submitter._get_criteria_for_type(alpha_type)
        submitter.criteria = criteria

        records = submitter.simulate_and_submit(
            alphas,
            region=region,
            universe=universe,
            auto_submit=False
        )

        # 显示结果
        passed = sum(1 for r in records if criteria.check(r.simulate_result))
        logger.info(f"\n{alpha_type} 结果: {passed}/{len(records)} 个符合标准")


def cmd_submit(args):
    """提交 Alpha 命令"""
    username, password = get_auth_credentials()
    client = create_client(username, password)

    generator = AlphaGenerator()

    # 生成 Alpha
    if args.type == 'all':
        alphas_by_type = generator.generate_all_types(
            regular_count=args.count,
            power_pool_count=args.count,
            atom_count=args.count,
            superalpha_count=args.count // 2
        )
    else:
        type_map = {
            'atom': generator.generate_atoms,
            'regular': generator.generate_regular_alphas,
            'power_pool': generator.generate_power_pool_alphas,
            'superalpha': generator.generate_superalphas
        }
        generator_fn = type_map.get(args.type, generator.generate_regular_alphas)
        alphas_by_type = {args.type: generator_fn(args.count)}

    # 解析区域和股票池
    region = Region(args.region.upper())
    universe = Unviverse.TOP3000

    # 创建提交器并设置标准
    criteria = SubmissionCriteria(
        min_sharpe=args.min_sharpe,
        min_fitness=args.min_fitness,
        max_turnover=args.max_turnover
    )

    submitter = AlphaSubmitter(client, criteria=criteria)

    # 批量提交
    results = submitter.batch_submit_by_type(
        alphas_by_type,
        region=region,
        universe=universe,
        auto_submit=True
    )

    # 汇总结果
    all_records = []
    for records in results.values():
        all_records.extend(records)

    # 生成报告
    report = submitter.generate_report(all_records)
    print(report)


def cmd_pending(args):
    """提交待处理 Alpha 命令"""
    username, password = get_auth_credentials()
    client = create_client(username, password)

    logger.info("获取待提交 Alpha 列表...")
    count = client.get_submittable_alphas()
    logger.info(f"找到 {len(count)} 个待提交 Alpha")

    if len(count) > 0:
        submitter = AlphaSubmitter(client)
        submitted = submitter.submit_pending_alphas()
        logger.info(f"成功提交 {submitted} 个 Alpha")


def cmd_generate(args):
    """仅生成 Alpha 表达式，不模拟"""
    generator = AlphaGenerator()

    type_map = {
        'all': lambda c: generator.generate_all_types(c, c, c, c//2),
        'atom': generator.generate_atoms,
        'regular': generator.generate_regular_alphas,
        'power_pool': generator.generate_power_pool_alphas,
        'superalpha': generator.generate_superalphas,
        '101': generator.generate_101_alphas_variations
    }

    generator_fn = type_map.get(args.type, generator.generate_regular_alphas)
    result = generator_fn(args.count)

    # 打印生成的表达式
    if isinstance(result, dict):
        for alpha_type, alphas in result.items():
            print(f"\n{'='*60}")
            print(f"{alpha_type.upper()} ({len(alphas)} 个)")
            print(f"{'='*60}")
            for i, alpha in enumerate(alphas, 1):
                print(f"\n{i}. {alpha['name']} ({alpha['category']})")
                print(f"   {alpha['expression']}")
    else:
        print(f"\n{'='*60}")
        print(f"{args.type.upper()} ({len(result)} 个)")
        print(f"{'='*60}")
        for i, alpha in enumerate(result, 1):
            print(f"\n{i}. {alpha['name']} ({alpha['category']})")
            print(f"   {alpha['expression']}")

    # 保存到文件
    if args.output:
        import json
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n已保存到: {args.output}")


def main():
    """主函数"""
    # 加载环境变量
    load_dotenv()

    parser = argparse.ArgumentParser(
        description='WorldQuant Brain Alpha 自动提交工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 模拟 10 个 Regular Alpha
  python main.py simulate -t regular -c 10

  # 提交 5 个 Power Pool Alpha
  python main.py submit -t power_pool -c 5

  # 提交所有类型并自定义标准
  python main.py submit -t all -c 5 --min-sharpe 1.5

  # 仅生成表达式（不模拟）
  python main.py generate -t 101 -c 10 -o alphas.json
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # simulate 命令
    simulate_parser = subparsers.add_parser('simulate', help='模拟 Alpha')
    simulate_parser.add_argument('-t', '--type', default='regular',
                                choices=['all', 'atom', 'regular', 'power_pool', 'superalpha'],
                                help='Alpha 类型')
    simulate_parser.add_argument('-c', '--count', type=int, default=5,
                                help='生成数量')
    simulate_parser.add_argument('-r', '--region', default='USA',
                                choices=['USA', 'CHN', 'EUR', 'JPN', 'TWN', 'KOR', 'GBR', 'DEU'],
                                help='交易区域')

    # submit 命令
    submit_parser = subparsers.add_parser('submit', help='模拟并提交 Alpha')
    submit_parser.add_argument('-t', '--type', default='regular',
                              choices=['all', 'atom', 'regular', 'power_pool', 'superalpha'],
                              help='Alpha 类型')
    submit_parser.add_argument('-c', '--count', type=int, default=5,
                              help='生成数量')
    submit_parser.add_argument('-r', '--region', default='USA',
                              choices=['USA', 'CHN', 'EUR', 'JPN', 'TWN', 'KOR', 'GBR', 'DEU'],
                              help='交易区域')
    submit_parser.add_argument('--min-sharpe', type=float, default=1.25,
                              help='最低 Sharpe 比率')
    submit_parser.add_argument('--min-fitness', type=float, default=0.7,
                              help='最低 Fitness')
    submit_parser.add_argument('--max-turnover', type=float, default=0.7,
                              help='最高换手率')

    # pending 命令
    pending_parser = subparsers.add_parser('pending', help='提交待处理 Alpha')

    # generate 命令
    generate_parser = subparsers.add_parser('generate', help='生成 Alpha 表达式')
    generate_parser.add_argument('-t', '--type', default='regular',
                                choices=['all', 'atom', 'regular', 'power_pool', 'superalpha', '101'],
                                help='Alpha 类型')
    generate_parser.add_argument('-c', '--count', type=int, default=10,
                                help='生成数量')
    generate_parser.add_argument('-o', '--output', type=str,
                                help='输出文件路径 (.json)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 执行命令
    commands = {
        'simulate': cmd_simulate,
        'submit': cmd_submit,
        'pending': cmd_pending,
        'generate': cmd_generate
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
