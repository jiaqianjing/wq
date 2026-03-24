# WQA CLI 命令参考

所有命令通过 `uv run wqa <command>` 执行。

## 生命周期

```bash
wqa init              # 生成 .wqa/config.yaml 模板
wqa init --force      # 覆盖已有配置
wqa start             # 后台启动 daemon + dashboard
wqa stop              # 停止 daemon
wqa restart           # stop + start
wqa status            # 查看 daemon 状态和队列摘要
```

## 知识同步

```bash
wqa account-info      # 探测 WQ 账号权限和真实提交门槛
wqa sync-knowledge    # 从 WQ API 拉取 operators 和 data fields
```

### `account-info` 输出示例

```json
{
  "username": "LJ68558",
  "genius_level": "GOLD",
  "onboarding": "CONSULTANT_APPROVED",
  "super_permitted": false,
  "available_regions": ["USA"],
  "available_delays": {"USA": [0, 1]},
  "real_submission_checks": {
    "LOW_SHARPE": 1.58,
    "LOW_FITNESS": 1.0,
    "HIGH_TURNOVER": 0.7
  }
}
```

探测内容：
- 用户身份（genius level、onboarding 状态）
- SUPER alpha 权限
- 可用区域（USA/CHN/EUR/ASI）
- 可用 delay（0/1）per region
- 真实提交门槛（从已有 alpha 的 checks 字段提取）

结果保存到 `brain_knowledge.yaml` 的 `account_profile` 字段，`_criteria()` 自动使用。

### `sync-knowledge` 输出示例

```json
{
  "operators": 84,
  "data_fields": 200,
  "knowledge_base": ".wqa/brain_knowledge.yaml"
}
```

拉取内容：
- 全部平台 operators（按类别分组）
- Top 200 data fields（按社区使用量排序）

## 典型工作流

```bash
# 首次设置
wqa init
# 编辑 .wqa/config.yaml 填入凭证

# 同步平台知识
wqa account-info
wqa sync-knowledge

# 启动
wqa start
# 打开 http://127.0.0.1:8765 查看 dashboard

# 日常维护
wqa status            # 检查运行状态
wqa restart           # 配置变更后重启

# 定期更新（建议每周）
wqa sync-knowledge
wqa restart
```

## 运行时文件

`wqa start` 后在 `.wqa/` 目录产生：

| 文件 | 用途 |
|---|---|
| `runtime.db` | 主数据库（ideas、experiments、events、agent 状态） |
| `runtime.json` | daemon 元数据（PID、启动时间、dashboard URL） |
| `wqa.pid` | PID 文件 |
| `alpha_history.db` | 历史 alpha 学习数据库 |
| `brain_knowledge.yaml` | 知识库（account-info + sync-knowledge + 手动维护） |
| `brain_operators.json` | 原始 operator 数据 |
| `brain_datafields.json` | 原始 data field 数据 |
| `logs/wqa.out.log` | stdout 日志 |
| `logs/wqa.err.log` | stderr 日志 |
| `submission_progress_*.json` | 每次 reviewer 提交的进度快照 |
