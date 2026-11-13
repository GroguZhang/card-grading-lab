# 本地化实现方案：自建 AI 卡况报告

> 目标：在自己的机器上跑出与 CardGrade 报告**同构**的分析结果，零订阅、零上传。
> 前置：`docs/report-teardown-zh.md`（报告拆解）与 `docs/ARCHITECTURE_ANALYSIS.md`（居中算法逆向）。

---

## 1. 先定边界：做什么，不做什么

拆解结论决定了一件事：这份报告里真正难的部分只有**语义缺陷判定**，其余（几何、评分、渲染）都是确定性工程。

因此方案的目标设为：

> **给自己一个"送评前的第二双眼睛"——把肉眼容易漏掉的角边面缺陷找出来、量化、留证，并给出保守的等级预期。**

明确不做：

| 不做 | 原因 | 替代 |
|---|---|---|
| 市场估值 / ROI | 需要成交数据库，不是算法问题 | 不做，或手填 |
| 卡牌自动识别到具体版别 | 需要几万张卡的图像库 | VLM 读卡面文字 + 人工确认 |
| 声称"等同官方评级" | 法律与事实都不成立 | 报告固定标注 self-assessment |
| 对外提供服务 | 避免 ToS 与合规风险 | 仅本机 / 局域网 |

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│  Web UI（复用现有 web-demo，扩展为报告页 + 队列页）             │
└───────────────────────────┬──────────────────────────────────┘
                            │ HTTP
┌───────────────────────────▼──────────────────────────────────┐
│  FastAPI 服务层                                               │
│   POST /api/grade        提交正反面 → 返回 report_id           │
│   GET  /api/report/{id}  取结构化结果（JSON）                  │
│   GET  /report/{id}      渲染 HTML 报告                        │
│   GET  /report/{id}.pdf  Playwright 打印                       │
└───┬──────────────┬──────────────┬──────────────┬──────────────┘
    │              │              │              │
┌───▼────┐  ┌──────▼──────┐  ┌────▼─────┐  ┌─────▼──────┐
│几何层   │  │语义层        │  │裁决层     │  │评分层       │
│OpenCV  │  │本地 VLM      │  │CV 亚像素  │  │规则引擎     │
│        │  │(逐区提问)    │  │复核       │  │            │
└───┬────┘  └──────┬──────┘  └────┬─────┘  └─────┬──────┘
    │              │              │              │
    └──────────────┴──────────────┴──────────────┘
                            │
              ┌─────────────▼─────────────┐
              │ SQLite（报告/缺陷/图片）    │
              │ 文件系统（原图 + 裁切证据） │
              └───────────────────────────┘
```

### 2.1 最关键的一个架构决策：逐区提问，不要整体打分

**错误做法**（也是最容易踩的坑）：

```
提示词：「给这张卡打分，并指出缺陷」
模型：「这张卡看起来是 9 分，边缘有些磨损……」
```

问题：不可复算、不可定位、无法与 CV 交叉验证、换个温度结果就变。

**正确做法**：把 VLM 当成**局部检测器**，而不是评分器。

```
对 8 个区（4 角 + 4 边）+ 1 个表面区，分别提问：
  「这是卡片左上角的放大图。是否存在露白(whitening)？
   是否存在角部触碰(corner touch)？是否分层(chipping)？
   只输出 JSON：{"whitening": "none|minor|major", "corner_touch": "...", "confidence": 0-1}」
```

这样得到的是一张**可定位、可复核、可累加**的缺陷表，而不是一句无法验证的结论。评分交给规则引擎。

这也是 CardGrade 自己的做法——它的 17 条信号每条都带 `Front · Top left · corner` 三段式定位，说明底层就是分区结果。

### 2.2 第二关键决策：AI 提议，CV 裁决

报告原文：*"CV measured signals ... can override a clean visual read."*

把它变成强制流程：

```
VLM 说"左上角有 minor 露白"
        ↓
裁出该角 128×128 区域
        ↓
CV 量化：白色像素占比？边缘粗糙度？对比度？
        ↓
占比 > 2% 且连续区域 > 40 px²  → 确认 minor
占比 < 0.5%                    → 否决，记为 VLM 误报
```

**CV 有一票否决权**。这一条能干掉 VLM 八成的幻觉，也是本方案与"随便调个多模态 API"的本质区别。

---

## 3. 核心模块与技术选型

### 3.1 模块清单

| 模块 | 职责 | 复用/新建 |
|---|---|---|
| `perspective_align.py` | 四点校正、去反光 | ✅ 已有 |
| `centering_calculator.py` | 内外框、居中比例、置信度 | ✅ 已有 |
| `grade_standards.py` | 六家阈值表 | ✅ 已有 |
| `zone_splitter.py` | 按 4 角 + 4 边 + 表面切区，输出带重叠的裁片 | 🆕 |
| `vlm_client.py` | 逐区提问、JSON 强约束、重试 | 🆕 |
| `cv_verifier.py` | 露白/毛边/划痕的亚像素量化 | 🆕 |
| `defect_model.py` | 缺陷事件 schema、严重度归一 | 🆕 |
| `scoring.py` | zone → subgrade → overall，含一致性惩罚 | 🆕 |
| `report_render.py` | Jinja2 模板 → HTML → PDF | 🆕 |
| `app/main.py` | FastAPI 路由、任务队列、存储 | 🆕 |

### 3.2 技术选型表

> **实施更新（2026-11-14）**：M2 实际落地时改用了 **DeepSeek API**，不再依赖本地模型。
> 原因是 `deepseek-flash` 已原生支持图片输入，成本约 **$0.0013/卡**，省掉 GPU、5 GB 权重与常驻服务。
> 下方 Ollama 方案保留为**完全离线**的备选（无网/隐私要求更高的场景）。
>
> | 维度 | DeepSeek API（已实现） | 本地 Ollama（备选） |
> |---|---|---|
> | 依赖 | 一个 API key | GPU ≥ 6 GB + 5 GB 权重 |
> | 单卡耗时 | 9 区并发，约 3–6 s | 串行约 40 s，并发约 10 s |
> | 单卡成本 | ≈ $0.0013 | 电费可忽略 |
> | 图片是否离开本机 | **是**（仅 9 张分区裁图） | 否 |
> | 判定质量 | 明显更强（能读"2mm 弧形磨损"这类描述） | 7B 级，定位可用、分级偏弱 |

| 环节 | 首选 | 备选 | 显存/依赖 | 选型理由 |
|---|---|---|---|---|
| 缺陷判定 | **DeepSeek `deepseek-flash`**（API） | Ollama + Qwen2.5-VL-7B | 一个 key / ≈6 GB | 免部署；原生图片输入；单卡亚分级成本 |
| — 图片送入方式 | **九分区裁图 704px** | 整卡 | — | 单图计费上限 1024 token，整卡多出的分辨率会被缩掉 |
| 几何/图像 | **OpenCV 4.x** | — | CPU | 已用于居中，无需新依赖 |
| 亚像素量化 | **OpenCV + scikit-image** | — | CPU | 连通域、形态学、局部对比度都齐 |
| 服务 | **FastAPI + uvicorn** | Flask | — | 自带 OpenAPI，前端好对接 |
| 存储 | **SQLite** | — | — | 单机自用，零运维 |
| 模板 | **Jinja2** | — | — | 复刻它的模板化文案 |
| PDF | **Playwright `page.pdf()` / 本机 Chrome** | WeasyPrint | 本机浏览器即可 | **直接复刻 iOS 打印那条链路**，CSS 即版式 |
| 前端 | 现有 `web-demo` 扩展 | — | — | 已有测量交互，加报告页即可 |

**关于模型的现实提醒**：
- 模型能力决定的是**定位候选**，不是精确分级——所以才有 §2.2 的 CV 裁决。
- 送图前先算账：整卡送进去 = 花 1024 token 换 1300px 等效，角部只剩几十像素；分区送才有毫米级细节。
- 如果一定要离线，用 2B 级模型 + 更强 CV，精度损失主要在语义层。

### 3.3 硬件匹配

| 机器 | 可行配置 | 预期速度 |
|---|---|---|
| **无 GPU / 笔记本** | **DeepSeek API 路线（推荐）** | 9 区并发，单卡 3–6 s |
| RTX 4070 (12 GB) | 本地 Qwen2.5-VL-7B Q4，9 区串行 | 约 3–6 s/区，单卡约 40 s |
| RTX 4080 (16 GB) | 7B FP8/AWQ，或 13B Q4；9 区可小批并发 | 约 1.5–3 s/区 |
| 无 GPU + 必须离线 | MiniCPM-V 2.6 Q4 或纯 CV 模式 | 约 20–40 s/区 |

---

## 4. 目录结构

```
card-grading-engine/
├── src/                        # 已有：几何 + 居中 + 阈值
├── app/
│   ├── main.py                 # FastAPI 入口
│   ├── pipeline.py             # 编排：校正→切区→VLM→CV→评分
│   ├── zone_splitter.py
│   ├── vlm_client.py
│   ├── cv_verifier.py
│   ├── defect_model.py
│   ├── scoring.py
│   ├── report_render.py
│   ├── templates/report.html.j2
│   ├── static/report.css
│   └── db.py
├── web-demo/index.html         # 已有：测量原型
├── data/
│   ├── cards.db
│   ├── uploads/{report_id}/front.jpg
│   └── crops/{report_id}/tl_corner.png
├── docs/
├── tests/
└── requirements.txt
```

---

## 5. 分阶段实施（每阶段可独立验收）

### M0 · 骨架（半天，不需要模型）

- 建 `app/` 目录、FastAPI 起服务、SQLite 建表
- `POST /api/grade` 只做一件事：存图 → 调 `auto_align` → 调 `detect_border_lines` → 存结果

**验收**：`curl` 上传一张卡，返回居中比例 JSON，与现有 `tests/run_checks.py` 结果一致。

### M1 · 报告渲染（1 天，不需要模型）

- 用 **假数据**（写死的缺陷表）跑通 `report.html.j2` → PDF
- 先把版式调对：卡砖、总评、四维、扫描摘要、CARD MAP、证据列表

**验收**：`GET /report/{id}.pdf` 产出的 PDF 在结构上与样本报告一一对应（页数、区块顺序、字号层级）。

> 这一步的价值：**在接模型之前就把"输出长什么样"钉死**。后面所有工作都是往里填数据。

### M2 · 语义层（已完成，DeepSeek API）

- `app/zone_splitter.py`：按校正后的卡图切 9 区（带 25% 外扩，防止缺陷压在切口上）
- `app/vision/deepseek.py`：逐区提问，`response_format=json_object` 强约束，空内容重试
- `app/vision/runner.py`：并发 + 裁图哈希缓存 + 单区失败隔离
- `app/vision/prompts.py`：缺陷闭集（10 种）+ 严重度三档 + 明确"看不清就说空"

**验收**：`python -m tests.test_vision` 23 项全过（假 provider，不花钱）；
真实调用用 `python -m tests.vision_probe --call` 跑一张卡，核对分区判定与 token 用量。

> 与原计划的差异：区数从 8 改为 9（把"画面中央"独立出来，表面缺陷才有归属）；模型从本地 7B 改为 API。

### M3 · 裁决与评分（2 天）

- `cv_verifier.py`：露白占比、边缘粗糙度、划痕响应
- `scoring.py`：实现 §4.1 的四层模型，**含跨区一致性惩罚**
- 对齐样本：同样的输入应当得到 Corners 9、overall 9

**验收**：`tests/test_scoring.py` 用样本数据断言 `overall == 9` 且 `limiting == "Corners"`。

### M4 · UI 与批处理（1 天）

- 前端：拖入正反面 → 进度 → 报告页
- 批量模式：一次喂一叠卡

---

## 6. 部署步骤

### 6.1 环境

```bash
cd card-grading-engine
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
pip install fastapi "uvicorn[standard]" jinja2 playwright pillow
playwright install chromium
```

### 6.2 缺陷判定（二选一）

**A. DeepSeek API（推荐，无需 GPU）**

```bash
export CARD_VISION_PROVIDER=deepseek
export DEEPSEEK_API_KEY=sk-...      # Windows: $env:DEEPSEEK_API_KEY='sk-...'

python -m tests.vision_probe         # 先看 footprint，不花钱
python -m tests.vision_probe --call  # 真跑一张，核对信号与用量
```

**B. 本地 Ollama（完全离线，需要 ≥6 GB 显存）**

```bash
ollama pull qwen2.5vl:7b        # 名称以官方库为准
ollama serve                     # 默认 127.0.0.1:11434
```

> 当前代码只实现了 A（`app/vision/deepseek.py`）。接 B 只需再写一个满足
> `app/vision/runner.py::VisionProvider` 协议的类（`inspect(req) -> (findings, usage)`），
> 评分与渲染层完全不用改——这正是把判定层单独拆出来的原因。

### 6.3 启动

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

打开 `http://127.0.0.1:8000`。

### 6.4 可选：Docker Compose

```yaml
services:
  ollama:
    image: ollama/ollama
    ports: ["11434:11434"]
    volumes: ["ollama:/root/.ollama"]
    deploy:
      resources:
        reservations:
          devices: [{ driver: nvidia, count: 1, capabilities: [gpu] }]

  app:
    build: .
    ports: ["8000:8000"]
    environment:
      OLLAMA_HOST: http://ollama:11434
    volumes: ["./data:/app/data"]
    depends_on: [ollama]

volumes:
  ollama:
```

---

## 7. 质量控制

| 风险 | 表现 | 对策 |
|---|---|---|
| VLM 幻觉 | 凭空报"左上角露白" | CV 否决权（§2.2）；同时要求模型给出置信度，< 0.6 降级为待确认 |
| VLM 过度宽容 | 明明有磨损却说"完美" | 提示词固定为"只回答看到什么"，禁止它评价等级 |
| 裁片偏差 | 缺陷压在分区切口上 | 20% 重叠 + 每区额外给一张"整卡缩略图"作上下文 |
| 光照影响 | 同一张卡两次结果不同 | 检测到高光占比 > 3% 时标记"建议重拍"，不硬出结论 |
| 评分漂移 | 阈值改了，历史报告不可比 | 报告里存 `model_version` + `threshold_version` |

**一条硬规则**：任何一维的置信度低于阈值，报告就把它标成 `需人工确认`，而不是给一个数字。CardGrade 在这一点上做得不够（四维全绿但其实是 9 分），我们不该复制这个缺陷。

---

## 8. 成本

| 项 | 成本 |
|---|---|
| 软件 | 0（全部开源） |
| 模型 | 0（本地推理） |
| 电费 | 单卡约 40 s × 250 W ≈ 0.003 度，可忽略 |
| 存储 | 每张卡约 3 MB（原图 + 裁片） |
| 时间 | M0–M1 约 1.5 天；M2–M3 约 4–5 天 |

对比：CardGrade $4.99/月起。自建的回本点在**约 3 个月的使用量**，真正的收益是没有上传、可复算、可改阈值。

---

## 9. 合规与自律

- 本方案为**个人自用**的研究与辅助工具，不对外提供服务、不收费、不分发。
- 不复制 CardGrade 的品牌、文案模板、视觉资产。版式可参考，文字必须自己写。
- 报告必须固定标注：*本地自评结果，非官方评级，不构成交易建议*。
- 不绕过任何付费墙或访问控制；不抓取其私有接口。

---

## 10. 下一步

按 §5 的顺序推进，**M0 与 M1 不需要任何模型**，可以立刻开工并当天看到 PDF。

建议先做 M1（报告渲染）——因为它把"目标长什么样"钉死，后面接模型时不会反复改版式。

需要我直接开始搭 M0 + M1 的话，说一声即可。
