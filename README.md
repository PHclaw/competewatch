# CompeteWatch - 竞品监控SaaS

自动监控竞争对手动态，第一时间发现价格变化、功能更新、文章发布等关键信息。

## 核心功能

### 监控类型
- **价格监控** — SaaS定价、电商价格变化
- **功能更新** — 产品Changelog、更新日志
- **文章发布** — 竞品博客、公众号文章
- **SEO排名** — 关键词搜索排名变化
- **社交媒体** — 微博/抖音/小红书动态

### 通知渠道
- 邮件通知
- 企业微信/钉钉 Webhook
- Telegram Bot
- 飞书机器人

### 数据分析
- 价格历史趋势图
- 更新频率统计
- 变化摘要报告

## 技术栈

- **后端**: FastAPI + SQLAlchemy + APScheduler
- **数据库**: SQLite (开发) / PostgreSQL (生产)
- **爬虫**: requests + BeautifulSoup / Playwright
- **前端**: 原生 HTML/CSS/JS

## 快速开始

```bash
# 安装依赖
cd backend
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 填入配置

# 启动服务
python main.py
```

访问 http://localhost:8080

## 项目结构

```
competewatch/
├── backend/
│   ├── main.py           # FastAPI 入口
│   ├── models.py         # 数据模型
│   ├── database.py       # 数据库配置
│   ├── scheduler.py      # 定时任务
│   ├── monitors/         # 监控模块
│   │   ├── price.py      # 价格监控
│   │   ├── changelog.py  # 更新日志
│   │   └── seo.py        # SEO排名
│   ├── notifiers/        # 通知模块
│   │   ├── email.py
│   │   └── webhook.py
│   └── requirements.txt
├── frontend/
│   └── index.html        # 管理后台
├── Dockerfile
└── docker-compose.yml
```

## API 文档

启动后访问 http://localhost:8080/docs

### 主要端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/competitors | 获取竞品列表 |
| POST | /api/competitors | 添加竞品 |
| GET | /api/competitors/{id}/changes | 获取变化历史 |
| POST | /api/competitors/{id}/check | 手动触发检查 |
| GET | /api/dashboard | 仪表盘数据 |

## 商业化

### 免费版
- 监控 3 个竞品
- 每日检查 1 次
- 邮件通知

### Pro版 (¥99/月)
- 监控 20 个竞品
- 每小时检查
- 全部通知渠道
- 历史数据 90 天

### 企业版 (¥299/月)
- 无限竞品
- 自定义检查频率
- API 接口
- 团队协作

## License

MIT
