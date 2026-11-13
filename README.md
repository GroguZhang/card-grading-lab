# card-grading-engine

球星卡居中测量与品相预评的开源工具库。纯本地计算，图片不上传。

这个仓库做了两件事：

1. **逆向研究**——拆解三家主流在线预评服务（CardGrade.io、Centering Check、Edge Grading）的图像处理链路，把它们的算法、参数、阈值表与交互设计整理成一份可验证的报告：[`docs/ARCHITECTURE_ANALYSIS.md`](docs/ARCHITECTURE_ANALYSIS.md)
2. **报告拆解**——拿到一份 CardGrade「CGI Vision AI」的真实报告 PDF，从 PDF 对象层反推它的生成链路、评分模型与文案机制：[`docs/report-teardown-zh.md`](docs/report-teardown-zh.md)
3. **本地化方案**——在单机上自建同类报告的完整实施路径（模块划分、技术选型、分阶段验收、部署命令）：[`docs/local-build-plan-zh.md`](docs/local-build-plan-zh.md)
4. **工程复刻**——用 Python + OpenCV 重建这套能力，并补上原产品缺失的三块：透视校正、亚像素精度、诚实降级。

---

## 核心结论（先看这个）

三家站点表面同类，底层是两条路线：

| 站点 | 处理位置 | 方法 | 透视校正 |
|---|---|---|---|
| CardGrade.io | 浏览器（2 个 Web Worker） | Sobel + 行列投影 | ❌ 靠"90° 平拍"规避 |
| Edge Grading | **同一套 Worker**（MD5 一致） | 同上 | ❌ 同上 |
| Centering Check | 服务端推理 | 未公开模型 | ✅ |

两条最值得知道的发现：

- **CardGrade 与 Edge Grading 共享同一份边缘检测代码**，`worker.edge-finder.js` 的 MD5 完全相同（`6b75d0b4…`）。
- 它们的灰度转换把 **R 与 B 的权重写反了**（`0.299·B + 0.587·G + 0.114·R`），这个 bug 同时存在于两家上线产品中。

---

## 特性

- **透视校正**：四点检测 → 边缘脊线拟合 → 单应变换到 63.5:88.9。拍歪了也能算。
- **亚像素边缘**：梯度加权质心，典型误差 ±0.2 个百分点。
- **六家阈值表**：PSA / BGS / CGC / SGC / TAG / EDGE，每档完整阶梯。
- **诚实降级**：边框过窄、宽高比异常、满版卡——都明确告警，而不是给个假数字。
- **零依赖前端**：`web-demo/index.html` 双击即用，含放大镜、可拖辅助线、实时比例。
- **缺陷判定可选**：默认纯离线（四角/边缘/表面标为「未评估」）；接上 DeepSeek API 后逐分区判定，单卡成本约 $0.001，无需 GPU 与本地模型。

---

## 快速开始

```bash
pip install opencv-python numpy
```

### 测量一张卡

```python
from src import auto_align, detect_border_lines, measure_from_borders

aligned = auto_align("card.jpg")                     # 自动找卡、拉正透视
borders, conf, _ = detect_border_lines(aligned.warped)
report = measure_from_borders(borders, confidence=conf)

print(report.ratio("lr"))        # '56.2/43.8'
print(report.ratio("tb"))        # '50.4/49.6'
print(report.summary())
print(report.grade_table())      # {'PSA': '9', 'BGS': '8', ...}
```

`measure_from_borders` 的参数就是四个内外边框坐标，也可以手动给：

```python
from src import BorderLines, measure_from_borders

b = BorderLines(
    outer_left=40,  inner_left=118, inner_right=880, outer_right=958,
    outer_top=52,   inner_top=140,  inner_bottom=1245, outer_bottom=1330,
)
report = measure_from_borders(b, confidence=1.0)
print(report.to_dict())
```

### 手头只有比例？

```python
from src import measure_from_ratios
report = measure_from_ratios((55.4, 44.6), (49.8, 50.2))
print(report.grade("PSA"))       # GradeTier(label='9', front=60, back=90)
print(report.is_borderline("PSA"))  # True —— 四舍五入到 55 会假通过
```

### 前端原型

直接用浏览器打开 `web-demo/index.html`，无需服务器、无需构建、无外部资源。

| 操作 | 效果 |
|---|---|
| 拖入 / `Ctrl+V` 粘贴 | 载入图片 |
| 拖四个角点 | 拉正透视 |
| 拖八条虚线 | 量内外边框 |
| `←→↑↓` | 微调 1 px（`Shift` 10 px） |
| `Tab` | 切换选中的线 |
| 滚轮 | 缩放视图 |
| 「自动检测」 | 投影剖面粗定位 |

前端不做像素重采样，而是把八条线经**单应矩阵**映射到「卡片已拉直」的坐标空间再算比例——和 Python 版同一套数学。

---

## 报告服务（`app/`）

`src/` 负责测量，`app/` 负责把它变成一份可以打印的中文报告。链路：

```
上传图片 → 透视校正 → 边框测量 → 缺陷信号 → 四层评分 → HTML → PDF
```

### 跑起来

```bash
pip install -r requirements.txt
python -m uvicorn app.main:app --port 8770
```

打开 <http://127.0.0.1:8770/> 上传，或直接看 <http://127.0.0.1:8770/demo>（合成卡 + 样本缺陷集，不需要图片）。

| 路由 | 用途 |
|---|---|
| `GET /` | 上传页 |
| `POST /api/grade` | 图片 → 报告，返回 `report_id` 与三个链接 |
| `GET /report/{id}` | HTML 报告 |
| `GET /report/{id}.pdf` | PDF 报告（首次访问现生成，之后走缓存） |
| `GET /api/report/{id}` | 结构化 JSON（含信号、分区、子分） |
| `GET /demo` | 演示报告 |

命令行等价物：

```bash
python -m tests.render_demo     # 合成卡 + fixture → examples/demo_report.pdf
python -m tests.test_scoring    # 评分引擎 6 项验收
```

### 评分模型：四层，每层都能被追问

```
缺陷信号（哪一角、什么缺陷、有多重、谁报的）
  └→ 分区分数（9 个区，单区扣分）
      └→ 维度子分（取最差区 + 跨区一致性惩罚）
          └→ 总评（四维取最差，映射等级与机构预测）
```

中间那层**跨区一致性惩罚**是从真实报告的矛盾数字反推出来的：分区 9.7 却给出子分 9，只差这一条规则解释不了。去掉惩罚复现出 9.5，加上得到 9——与样本报告一致（`tests/test_scoring.py` 里有一条专门的敏感性测试守住它）。

机构预测不是把总分换个皮：`predictions` 保留了 PSA / BGS / CGC 之间已知的系统性松紧差（`ScoringConfig.prediction_offsets`）。

### 报告是模板，不是作文

报告里每一句话都由数字推导：`_story_zh` 只陈述"哪些维度达到顶级档"（来自子分）与"限制项两侧各多少条信号"（来自信号表）。没有大模型参与写作，所以同一份输入永远得到同一份文本，也不会出现"居中无可测量偏差"配着 49/51 这种自相矛盾——那正是原产品被拆出来的问题（见 [`docs/report-teardown-zh.md`](docs/report-teardown-zh.md)）。

### 没测就写"没测"

未启用视觉判定时，四角 / 边缘 / 表面不做检查。这时报告显示 `—` 而不是 `10`，并附一行说明：总评按"该维度无缺陷"计算，这些 10 分是默认值。同一原则也用在扫描摘要（`未评估` ≠ `未检出`）。

### PDF 走浏览器打印，不是 PDF 绘图库

版式是 CSS，`@page { size: A4 }` 定纸张，两条引擎依次尝试：

1. Playwright + 本机 Chrome / Edge（`channel="chrome"`，无需下载 150 MB Chromium）
2. 直接调用本机浏览器的 `--headless=new --print-to-pdf`
3. 都失败则显式报错并提示用浏览器 `Ctrl+P`

失败不会被吞掉——`render_pdf` 返回 `(ok, message)`，接口据此回 503 并说明原因。

---

## 缺陷判定：调 API，不装模型

M2 原本的设计是本地 Ollama + Qwen2.5-VL-7B。实际接 DeepSeek 更划算：**`deepseek-flash` 原生支持图片输入**，一个 API key 就能跑，不用 GPU、不用下 5 GB 权重、不用守着机器。

### 一个决定架构的数字

官方文档写明：图片在推理前会被缩放到约 **1300×1300 等效**，单图最多计 **1024 token**。也就是说把 4000px 的原图整张丢进去，多出来的分辨率全被丢掉，钱还照付。

所以送的不是整卡，而是**九张分区裁图**（默认 704 px）：

| 送法 | 有效分辨率 | 单卡图片 token | 能不能看清 2mm 露白 |
|---|---|---|---|
| 整卡 4000px | 被压到 1300 等效 | 1024 | ❌ 角部只剩几十像素 |
| 九分区 704px | 每张都不触发缩放 | 9 × ~700 | ✅ 角部占据画面主体 |

成本量级：一张卡 9 张裁图约 6.3K 输入 + 0.5K 输出，按 off-peak 价（$0.15 / $0.60 每百万 token）约 **$0.0013**。一千张卡一块三。

### 开起来

```bash
export CARD_VISION_PROVIDER=deepseek
export DEEPSEEK_API_KEY=sk-...        # Windows: $env:DEEPSEEK_API_KEY='sk-...'

python -m tests.vision_probe          # 只看会发什么，不花钱
python -m tests.vision_probe --call   # 真跑一次，打印信号与 token
```

其余开关见 [`.env.example`](.env.example)。默认是 `none`：**不配就是离线**，报告里四角/边缘/表面显示为「—」。

| 变量 | 默认 | 说明 |
|---|---|---|
| `CARD_VISION_PROVIDER` | `none` | `none` / `deepseek` |
| `DEEPSEEK_API_KEY` | — | 只从环境读，不写日志、不入库 |
| `CARD_VISION_MODEL` | `deepseek-flash` | `deepseek-v4-pro` 只支持文本，不能用于图片 |
| `CARD_VISION_ZONE_SIZE` | `704` | 再大也会被缩到 1300 等效 |
| `CARD_VISION_INCLUDE_BACK` | `0` | 背面送检成本翻倍 |
| `CARD_VISION_CACHE_DIR` | `data/cache/vision` | 按裁图哈希缓存，重跑不再付费 |

### 什么会离开本机

只有**九张分区裁图**。原图、校正后的整卡、居中比例、复算用的边框坐标都不上传——居中永远是本地几何测量，模型无权过问。

这条边界写在报告页脚里，不是只写在 README 里：

> 视觉判定由 deepseek/deepseek-flash 完成：检查 9 个分区，得到 3 条信号；9 次请求 / 9 张图 / 6312 输入 + 482 输出 token，约 $0.0011。
> 本报告的四角、边缘、表面由云端模型判定，9 张分区裁图已上传至 https://api.deepseek.com。居中比例仍为本地几何测量，未经上传。

### 工程上必须处理的四件事

1. **单区失败不能拖垮整卡。** 九个并发请求，总有一两个撞 429。失败的分区记为「未评估」，其余照常出报告，错误原文进警告列表。
2. **JSON 模式会返回空白。** 官方文档明确：非 JSON 内容被替换成空格，`finish_reason` 仍是 `stop`。空内容当失败重试，不能当成"没有缺陷"。
3. **缺陷名是闭集。** 模型只能从十个名字里选。返回 `worn corner` 这种自创词直接判失败——悄悄映射成 `corner_touch` 只会把问题藏起来。
4. **缓存按裁图哈希。** 同一张卡重跑读本地 JSON，不产生费用；改了 prompt 版本号才重新请求。

### 「未评估」不是「未检出」

没开视觉判定时，四角/边缘/表面的 10 分是**默认值**，不是测量结果。报告里显示 `—`，摘要写「未评估」。跑了判定但确实没发现缺陷，才写「未检出」。

同理，一个维度给出 10 分却带着缺陷信号时（模型允许轻微缺陷保留 10 档，与参考报告一致），报告会明说：

> 这 1 条信号都在 10 档容差之内，子分保留 10 分：轻微缺陷扣分但不淘汰档位，请勿把 10 读成"无瑕"。

---

## 算法原理

居中本身是除法：

```
left_pct = (inner_left − outer_left) / (left_width + right_width) × 100
worst    = max(left_pct, right_pct, top_pct, bottom_pct)
```

难的是**找到那八条线**，以及**知道找错了**。

### 为什么用投影剖面而不是轮廓追踪

卡片边框在几何上就是一条长直线。把水平梯度沿列求和，一条真实边框会让上百个像素同时"投票"，形成一个尖峰；划痕和噪点永远凑不够票数。**噪声被免费过滤**，不需要形态学清理。

代价是该方法要求卡片**轴对齐**——所以在检测之前必须做透视校正。

### 三步流程

```
① 粗检测    Canny / Otsu / 自适应阈值三路并行 → 候选四边形 → 按面积/宽高比/倾角打分
② 亚像素精修 每条边取一条带 → 梯度脊线 → Huber 拟合直线 → 四线求交得角点
③ 变换      四角 → 63.5:88.9 矩形，输出带 margin 的拉正图
```

最后那个 margin 不是装饰：卡片如果贴着图像边界，就没有梯度可供检测。

### 一个容易被忽略的偏置

3×3 Sobel 对理想阶跃边缘**不产生单峰，而产生一对对称双峰**。取其中较大的那个，会让测量系统性偏向卡片外侧；偏置是固定亚像素量，所以边框越窄、占比越大——表现为 50/50 的卡只差 0.06 pp，70/30 的卡差 0.70 pp。

解法是对峰值邻域取**梯度加权质心**：对称双峰的质心正好落在真实边缘上。

---

## 精度

合成卡真值验证（`python -m tests.run_checks`）：

```
worst L/R error 0.70 pp   worst T/B error 0.23 pp
```

- 典型误差 **±0.2 pp**
- 最差 **±0.7 pp**（边框仅 51 px 时）
- 透视校正后误差 ≤ 0.3 pp

作为对照：PSA 10 与 9 的正面门槛相差 5 pp，所以这个精度足够区分相邻等级，但在 **±0.5 pp 的临界区**必须看 `is_borderline()` 的提示——四舍五入会翻转结论。

---

## 阈值表

`src/grade_standards.py` 收录六家机构的完整阶梯。顶级档速查：

| 机构 | 等级 | 正面 | 背面 |
|---|---|---|---|
| PSA | Gem Mint 10 | 55/45 | 75/25 |
| BGS | Black Label 10 | 50/50 | 55/45 |
| BGS | Gem Mint 9.5 | 55/45 | 60/40 |
| CGC | Pristine 10 | 50/50 | 50/50 |
| CGC | Gem Mint 10 | 55/45 | 75/25 |
| SGC | Pristine 10 | 50/50 | 50/50 |
| SGC | 10 | 55/45 | 55/45 |
| TAG | 10 Pristine | 51/49 | 52/48 |

读法：`55/45` = 较宽一侧最多占 55%。完整阶梯见[报告第 6 节](docs/ARCHITECTURE_ANALYSIS.md#6-评级机构居中容差对照表)。

> ⚠️ 这些数字来自各机构公开分级页与第三方交叉核对，**不构成官方承诺**。评级公司会改标准（PSA 2025 年就把 GM10 正面从 60/40 收紧到 55/45）。送评前请自行复核。

---

## 目录结构

```
card-grading-engine/
├── docs/
│   ├── ARCHITECTURE_ANALYSIS.md   竞品逆向研究报告
│   ├── report-teardown-zh.md      真实报告 PDF 拆解
│   └── local-build-plan-zh.md     本地化实施方案
├── src/                           测量内核
│   ├── perspective_align.py       透视校正：四角检测 / 精修 / 变换 / 去反光
│   ├── centering_calculator.py    投影剖面找边 / 居中计算 / 等级映射
│   └── grade_standards.py         六家阈值阶梯（数据与逻辑分离）
├── app/                           报告服务
│   ├── config.py                  环境变量 → 运行配置（含视觉层开关）
│   ├── defect_model.py            信号 / 分区 / 维度 / 报告 数据模型
│   ├── scoring.py                 四层评分引擎
│   ├── zone_splitter.py           九分区切图
│   ├── pipeline.py                测量 + 判定 + 评分 + 证据图
│   ├── vision/                    缺陷判定层（可插拔）
│   │   ├── deepseek.py            deepseek-flash 调用、重试、JSON 校验
│   │   ├── prompts.py             逐分区提示词与缺陷闭集
│   │   ├── runner.py              并发、缓存、部分失败隔离
│   │   └── types.py               请求 / 发现 / 用量
│   ├── report_render.py           Jinja2 → HTML → PDF
│   ├── db.py                      SQLite 存档
│   ├── main.py                    FastAPI 路由
│   ├── templates/report.html.j2   中文报告模板
│   └── static/report.css          深色仪表盘版式 + 打印规则
├── fixtures/
│   └── sample_signals.json        从真实报告重建的 17 条信号 + 期望值
├── web-demo/
│   └── index.html                 零依赖单文件前端
├── tests/
│   ├── synthetic.py               合成卡生成（已知居中比例）
│   ├── run_checks.py              精度自检
│   ├── test_scoring.py            评分引擎 6 项验收
│   ├── test_vision.py             视觉层 23 项验收（假 provider，不花钱）
│   ├── vision_probe.py            视觉层探测：先看footprint，再决定是否付费
│   └── render_demo.py             端到端渲染验收（HTML + PDF）
└── examples/                      示例输出
```

---

## 与竞品的差异

| | CardGrade / Edge | Centering Check | 本仓库 |
|---|---|---|---|
| 透视校正 | ❌ 要求重拍 | ✅ | ✅ 四点 + 亚像素精修 |
| 亚像素 | ❌ 整像素 | 未公开 | ✅ 梯度质心 |
| 百分比取整 | ❌ 取整 | 保留 1 位 | ✅ 不取整 |
| 置信度 | ❌ 无 | 未公开 | ✅ 每张图输出 |
| 满版卡 | ❌ 回退默认值 | 模型猜测 | ✅ 明确拒绝出分 |
| 运行位置 | 浏览器 | 服务端 | 本地 |
| 阈值可改 | ❌ 写死 | ❌ | ✅ 数据文件 |

最后一行可能是最实用的：`STANDARDS` 是个普通字典，机构改标准时改一行数字即可，不用重新发版。

---

## 免责声明

- 这是**预筛工具**，不是官方评级。评级公司检验实物。
- 居中只是四维评分之一（另有四角、边缘、表面）。居中满分不代表总分满分。
- 毫米读数是**推定值**：它假设检测到的外框跨度等于标准卡 63.5 × 88.9 mm。
- `inpaint_glare()` 会伪造像素，**只能用于解锁边框检测，绝不能用来评估表面品相**。
- 本仓库代码为独立编写，未包含任何第三方站点源码。研究报告基于公开前端资源，结论为逻辑重建而非源码还原。请勿用于商业克隆或绕过任何服务限制。

---

## License

MIT
