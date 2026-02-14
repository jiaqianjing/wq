#!/usr/bin/env python3
"""
智能生成命令 - 基于历史数据学习

使用方法:
  python smart_generate.py analyze    # 分析历史数据
  python smart_generate.py suggest    # 建议下一批生成
  python smart_generate.py run        # 执行智能生成
"""

import os
import sys
import argparse
import logging
from pathlib import Path

from dotenv import load_dotenv

from wq_brain import WorldQuantBrainClient, AlphaGenerator, AlphaSubmitter
from wq_brain.client import Region, Unviverse
from wq_brain.learning import AlphaDatabase, AlphaAnalyzer, SmartGenerator

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def cmd_analyze(args):
    """分析历史数据"""
    db = AlphaDatabase(args.db_path)
    analyzer = AlphaAnalyzer(db)

    # 生成报告
    report = analyzer.generate_report(args.output)
    print(report)

    # 如果指定了模板，分析该模板的参数
    if args.template:
        print(f"\n{'='*80}")
        print(f"模板 '{args.template}' 参数分析")
        print(f"{'='*80}")

        param_stats = analyzer.analyze_parameters(args.template)
        for param_name, values in param_stats.items():
            print(f"\n参数: {param_name}")
            print(f"{'值':<15} {'总数':>8} {'成功':>8} {'成功率':>10} {'平均Sharpe':>12}")
            print("-" * 60)
            for value, stats in values.items():
                print(
                    f"{str(value):<15} {stats['total']:>8} {stats['success']:>8} "
                    f"{stats['success_rate']*100:>9.1f}% {stats['avg_sharpe']:>12.3f}"
                )


def cmd_suggest(args):
    """建议下一批生成"""
    db = AlphaDatabase(args.db_path)
    analyzer = AlphaAnalyzer(db)
    smart_gen = SmartGenerator(analyzer)

    # 获取建议
    suggestions = smart_gen.suggest_next_batch(args.type, args.count)

    if not suggestions:
        print("暂无历史数据，无法提供建议")
        print("请先运行一些模拟以积累数据")
        return

    print(f"\n{'='*80}")
    print(f"智能生成建议 - {args.type.upper()} 类型")
    print(f"{'='*80}")
    print(f"\n建议生成 {len(suggestions)} 个 Alpha:")

    # 统计策略分布
    strategy_count = {}
    for s in suggestions:
        strategy = s['strategy']
        strategy_count[strategy] = strategy_count.get(strategy, 0) + 1

    print(f"\n策略分布:")
    print(f"  - 利用 (exploit): {strategy_count.get('exploit', 0)} 个 - 使用高成功率模板")
    print(f"  - 平衡 (balanced): {strategy_count.get('balanced', 0)} 个 - 使用中等成功率模板")
    print(f"  - 探索 (explore): {strategy_count.get('explore', 0)} 个 - 随机探索新模板")

    # 显示推荐模板
    print(f"\n推荐模板:")
    template_count = {}
    for s in suggestions:
        template = s['template']
        template_count[template] = template_count.get(template, 0) + 1

    for template, count in sorted(template_count.items(), key=lambda x: x[1], reverse=True):
        print(f"  - {template}: {count} 个")

    # 获取模板权重
    print(f"\n模板权重分析:")
    weights = smart_gen.get_template_weights(args.type)
    sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)[:10]

    print(f"{'模板名称':<40} {'权重':>10}")
    print("-" * 52)
    for template, weight in sorted_weights:
        print(f"{template:<40} {weight:>10.4f}")


def cmd_run(args):
    """执行智能生成"""
    # 加载认证
    username = os.getenv('WQB_USERNAME')
    password = os.getenv('WQB_PASSWORD')

    if not username or not password:
        logger.error("请设置环境变量 WQB_USERNAME 和 WQB_PASSWORD")
        sys.exit(1)

    # 初始化
    db = AlphaDatabase(args.db_path)
    analyzer = AlphaAnalyzer(db)
    smart_gen = SmartGenerator(analyzer)
    generator = AlphaGenerator()

    # 获取建议
    suggestions = smart_gen.suggest_next_batch(args.type, args.count)

    if not suggestions:
        logger.warning("暂无历史数据，使用随机生成")
        # 回退到普通生成
        type_map = {
            'atom': generator.generate_atoms,
            'regular': generator.generate_regular_alphas,
            'power_pool': generator.generate_power_pool_alphas,
            'superalpha': generator.generate_superalphas
        }
        generator_fn = type_map.get(args.type, generator.generate_regular_alphas)
        alphas = generator_fn(args.count)
    else:
        # 基于建议生成
        logger.info(f"基于历史数据智能生成 {len(suggestions)} 个 Alpha")

        # TODO: 这里需要根据建议的模板和参数分布生成
        # 目前先使用普通生成，后续可以增强
        type_map = {
            'atom': generator.generate_atoms,
            'regular': generator.generate_regular_alphas,
            'power_pool': generator.generate_power_pool_alphas,
            'superalpha': generator.generate_superalphas
        }
        generator_fn = type_map.get(args.type, generator.generate_regular_alphas)
        alphas = generator_fn(args.count)

    # 创建客户端和提交器
    client = WorldQuantBrainClient(username, password)
    if not client.authenticate():
        logger.error("认证失败")
        sys.exit(1)

    submitter = AlphaSubmitter(client, enable_learning=True)

    # 解析区域
    region = Region(args.region.upper())
    universe = Unviverse.TOP3000

    # 模拟并提交
    records = submitter.simulate_and_submit(
        alphas,
        region=region,
        universe=universe,
        auto_submit=args.submit
    )

    # 生成报告
    report = submitter.generate_report(records)
    print(report)

    logger.info(f"结果已保存到学习数据库: {args.db_path}")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(
        description='智能 Alpha 生成系统 - 基于历史数据学习',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='可用命令')

    # analyze 命令
    analyze_parser = subparsers.add_parser('analyze', help='分析历史数据')
    analyze_parser.add_argument('--db-path', default='./results/alpha_history.db',
                               help='数据库路径')
    analyze_parser.add_argument('-o', '--output', type=str,
                               help='保存报告到文件')
    analyze_parser.add_argument('--template', type=str,
                               help='分析特定模板的参数')

    # suggest 命令
    suggest_parser = subparsers.add_parser('suggest', help='建议下一批生成')
    suggest_parser.add_argument('--db-path', default='./results/alpha_history.db',
                               help='数据库路径')
    suggest_parser.add_argument('-t', '--type', default='regular',
                               choices=['atom', 'regular', 'power_pool', 'superalpha'],
                               help='Alpha 类型')
    suggest_parser.add_argument('-c', '--count', type=int, default=10,
                               help='建议数量')

    # run 命令
    run_parser = subparsers.add_parser('run', help='执行智能生成')
    run_parser.add_argument('--db-path', default='./results/alpha_history.db',
                           help='数据库路径')
    run_parser.add_argument('-t', '--type', default='regular',
                           choices=['atom', 'regular', 'power_pool', 'superalpha'],
                           help='Alpha 类型')
    run_parser.add_argument('-c', '--count', type=int, default=10,
                           help='生成数量')
    run_parser.add_argument('-r', '--region', default='USA',
                           choices=['GLB', 'USA', 'CHN', 'EUR', 'JPN', 'TWN', 'KOR', 'GBR', 'DEU'],
                           help='交易区域')
    run_parser.add_argument('--submit', action='store_true',
                           help='自动提交符合条件的 Alpha')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 执行命令
    commands = {
        'analyze': cmd_analyze,
        'suggest': cmd_suggest,
        'run': cmd_run
    }

    cmd_func = commands.get(args.command)
    if cmd_func:
        cmd_func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
