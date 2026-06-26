---
name: search-router
description: 统一搜索入口。根据查询类型自动路由到 anysearch/firecrawl/a-stock-data/westock-*/search-skills-sh。用户说"搜索/查找/查一下"时触发。
agent_created: true
---

# 搜索路由 — 统一入口

老大说"搜/查/找"时，按以下规则自动分发到正确的后端。**不要同时加载多个搜索 skill，路由到哪个就加载哪个。**

## 路由规则（从上到下匹配，命中即停）

### 1. 网页抓取 → firecrawl
**触发条件：** 用户提供 URL 要求抓取/爬取/提取内容，或要求与动态页面交互（填表单、登录等）。
```
"抓这个页面 https://..."
"把这个网站内容爬下来"
"提取这篇文章的内容"
```

### 2. 社区 Skill 搜索 → search-skills-sh
**触发条件：** 用户要在 skills.sh 社区找 skill。
```
"skills.sh 上有没有 Git 相关的 skill"
"社区搜一下 Python skill"
```

### 3. A 股深度数据 → a-stock-data
**触发条件：** 涉及 A 股个股行情、K 线、估值、研报、龙虎榜、资金流向、公告、财报三表、股东户数、分红、概念板块、北向资金、行业对比等。**关键词含股票代码（6 位数字）或明确 A 股标的。**
```
"查一下 688017 的 PE 和 PEG"
"贵州茅台最近研报"
"今天龙虎榜"
"北向资金流向"
```

### 4. 选股/筛选/排行榜 → westock-tool
**触发条件：** 按条件批量筛选股票或基金、找排行榜。
```
"找高股息 ETF"
"MACD 金叉的股票"
"科创 50 成分股排行"
```

### 5. 跨市场金融 / 宏观 / 非A股 → wb-finance-skill
**触发条件：** 港股、美股、外汇、期货、宏观数据、或金融数据但不在 A 股深度覆盖范围内。
```
"特斯拉美股财报"
"美元兑人民币汇率"
"CPI 数据"
```

### 6. 通用搜索 / 代码 / 学术 / 技术 / 法律 / 安全 → anysearch
**触发条件：** 所有不命中上述规则的搜索请求。覆盖代码文档、学术论文、技术问题、安全漏洞、法律案例等。
```
"搜一下 ONNX Runtime 最新版本"
"transformer 注意力机制论文"
"React hooks 最佳实践"
```

## 路由优先级速查

```
URL抓取/爬取 → firecrawl
skills.sh搜skill → search-skills-sh
A股行情/研报/龙虎榜/资金流 → a-stock-data
选股/筛选/排行 → westock-tool
港股/美股/外汇/宏观 → wb-finance-skill
其他一切搜索 → anysearch
```

## 实现注意

1. 命中规则后，用 Skill 工具加载对应 skill，不要手动构造命令
2. 如果路由到的 skill 返回"无法处理"，按优先级向下 fallback
3. anysearch 是兜底，覆盖所有未命中场景
