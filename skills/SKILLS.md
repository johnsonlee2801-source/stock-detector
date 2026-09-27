# watching.flowhunt.net · 技能手册

2026-09-27 起，这个仓库的页面从"美港股连续放量下跌检测器"换成了
**加密货币波动率压缩观察站**。旧的 `detector.py` 还留在仓库里但不再运行。

## 规则
- 改了 `vol_scan.py` / `watchlist.txt` / 页面模板 → 跑 `skills/deploy_vol.py`
- 想立刻刷新数据 → 跑 `skills/trigger.py`（或到 Actions 页面点 Run workflow）
- 想看是否正常 → 跑 `skills/status.py`（它还会报 detector.py 的 SHA，忽略即可）
- 每次改代码后必须验证网站更新，不需要再问

## 部署
```bash
python3 ~/Desktop/美港股检测器/skills/deploy_vol.py           # 本地先跑一遍扫描再上传
python3 ~/Desktop/美港股检测器/skills/deploy_vol.py --no-scan # 只传代码，让 Actions 生成报告
```
- Token 从钥匙串 `stock-detector-token` 读，没有就读项目根目录 `.github_token`
- 本机的 `gh` 登录已过期，`git` 因为 Xcode 许可没同意也用不了，所以部署走 GitHub API
- 改 `.github/workflows/scan.yml` 需要 Token 带 `workflow` 权限，失败就在 GitHub 网页手动贴

## 自动扫描时间（GitHub Actions）
| 时间 | 说明 |
|------|------|
| 每天 08:20 北京时间 | 币安日线 UTC 零点收盘后 |

## 关键文件
| 文件 | 用途 |
|------|------|
| `vol_scan.py` | 扫描逻辑 + HTML 模板，只用标准库 |
| `watchlist.txt` | 自选币，页面上带 ★ |
| `report.html` | 生成的页面（Actions 自动提交） |
| `vol_data.json` | 每个币的完整指标（含 60 日 bbw_pct 曲线） |
| `vol_history.json` | 每天一条汇总：中位数、极度压缩币数等，画历史图用 |
| `.github/workflows/scan.yml` | GitHub Actions 定时任务 |
| `skills/deploy_vol.py` | 部署脚本 |
| `detector.py` | 旧的美港股检测器，已停用 |

## 数据源
- 主：`data-api.binance.vision`（币安公共数据镜像，美国机器可访问），不通再试 `api.binance.com`
- 兜底：OKX 公共行情接口（币安没有的币，如 OKB）
- 两边都没有的币（如 GT、BGB）会在页面底部列出

## 本地手动跑
```bash
cd ~/Desktop/美港股检测器
/opt/anaconda3/bin/python3 vol_scan.py ETH SOL NEAR                   # 看终端表格
/opt/anaconda3/bin/python3 vol_scan.py --top-volume 100 --file watchlist.txt --html report.html
```

## 仓库信息
- GitHub: https://github.com/johnsonlee2801-source/stock-detector
- 网站: https://watching.flowhunt.net/report.html
- Actions: https://github.com/johnsonlee2801-source/stock-detector/actions
