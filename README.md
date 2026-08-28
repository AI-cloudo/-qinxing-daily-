# 禽类行情日报 · 云端部署指南

## 这套东西是什么

- `scrape.py`：每天自动联网抓取鸡病专业网（jbzyw.com）的最新行情——鸡蛋+淘汰鸡分省汇总、白羽肉鸡棚前价、各地麻鸡/公鸡产区报价、817肉杂
- `render.py` + `data.json`：生成你熟悉的那个手机页面（版式与现在完全一致，含自动刷新）
- `state.json`：记录上一次的价格，用于自动计算每日环比 ↑↓
- `.github/workflows/daily.yml`：GitHub 的定时任务，每天**北京时间 6:50 和 7:50 各跑一次**，抓完自动发布，全程不需要你的电脑开机，不消耗任何积分

**注意：三黄鸡快大类各省价、黑凤公鸡价等鸡病专业网没有的数据，云端会沿用最近一次数据并在页面上标注数据日期，绝不会冒充当日价。**

---

## 第一步：注册 GitHub（约 3 分钟）

1. 打开 **github.com**，点右上角 **Sign up**
2. 输入邮箱 → 设置密码 → 用户名（随便起，比如 `chickenbuyer`）
3. 按提示完成邮箱验证

## 第二步：创建仓库（约 1 分钟）

1. 登录后，点右上角 **+** → **New repository**
2. Repository name 填：`qinxing-daily`
3. 选 **Public**（免费）
4. 点绿色按钮 **Create repository**

## 第三步：上传 6 个文件（约 2 分钟）

1. 在新建好的空仓库页面，找到 **"uploading an existing file"** 链接，点它
2. 打开电脑上的这个文件夹：`WorkBuddy/2026-08-26-10-07-56/cloud-report/`
3. 把里面这 6 个文件**一起拖进浏览器**上传框：
   - `index.html`
   - `data.json`
   - `state.json`
   - `render.py`
   - `scrape.py`
   - `README.md`
4. 拖完后点绿色按钮 **Commit changes**

## 第四步：创建定时任务文件（约 2 分钟，关键步骤）

1. 仓库页面点 **Add file** → **Create new file**
2. 文件名框里输入（直接完整复制粘贴，斜杠会自动变成文件夹）：
   ```
   .github/workflows/daily.yml
   ```
3. 内容框粘贴以下全部内容：

```yaml
name: 每日禽类行情云端更新

on:
  schedule:
    - cron: '50 22 * * *'
    - cron: '50 23 * * *'
  workflow_dispatch: {}

permissions:
  contents: write
  pages: write
  id-token: write

concurrency:
  group: daily-report
  cancel-in-progress: false

jobs:
  update-and-deploy:
    runs-on: ubuntu-latest
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - name: 检出仓库
        uses: actions/checkout@v4

      - name: 安装 Python
        uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: 抓取最新行情并生成页面
        run: |
          python scrape.py
          python render.py

      - name: 把最新数据提交回仓库
        run: |
          git config user.name "auto-report-bot"
          git config user.email "actions@github.com"
          git add data.json state.json index.html
          git diff --cached --quiet || git commit -m "自动更新 $(date -u +'%Y-%m-%d %H:%M') UTC"
          git push

      - name: 配置 GitHub Pages
        uses: actions/configure-pages@v5

      - name: 上传页面
        uses: actions/upload-pages-artifact@v3
        with:
          path: '.'

      - name: 发布到 GitHub Pages
        id: deployment
        uses: actions/deploy-pages@v4
```

4. 点绿色按钮 **Commit changes**

## 第五步：开启 Pages（约 1 分钟）

1. 仓库页面点顶部 **Settings** → 左侧找到 **Pages**
2. **Build and deployment** 下的 **Source** 选择 **GitHub Actions**
3. 自动保存即可

## 第六步：手动跑一次，验证成功（约 3 分钟）

1. 仓库页面点顶部 **Actions** 标签
2. 左侧点 **每日禽类行情云端更新** → 如果有 **Enable workflow** 按钮就点它启用
3. 点右侧 **Run workflow** → 绿色 **Run workflow** 按钮
4. 等约 2 分钟，出现绿色 ✓ 表示成功
5. 你的手机页面地址就是：

   **https://你的用户名.github.io/qinxing-daily/**

   （把这个链接发到微信里，用手机浏览器打开，然后"添加到主屏幕"，以后点图标直接看）

## 之后每天

- 北京时间 **6:50 / 7:50** 云端自动抓取并发布，8 点前完成
- 页面打开时会自动检查新版（每 2 分钟），有更新自动刷新
- 完全不依赖你的电脑，电脑关机也照常更新，**零积分消耗**

## 常见问题

- **Actions 里是红色 ✗**：点进失败的那次运行看日志。多半是抓取的网站临时不通——脚本设计了容错（抓不到的板块沿用上次数据），如果整体失败，等下一次 7:50 自动重跑即可；连续失败再找我
- **页面打不开/很慢**：github.io 在个别网络下偏慢，换个网络环境试试；若长期不便，后续可以换托管
- **想立即手动更新**：Actions → 每日禽类行情云端更新 → Run workflow
