# CardGrade「CGI Vision AI」报告拆解

> 样本：`Stephen Curry — Grade 9 Mint  CardGrade.pdf`（2026-11-13 生成，报告 ID `0870F85F`）
> 方法：PDF 对象级取证 + 版式逐屏还原 + 文案与数据交叉比对
> 结论性质：基于单一公开样本的结构与逻辑重建，非源码还原

---

## 0. 一句话结论

这份报告**不是服务端用 PDF 库排版出来的，而是一个响应式网页被 iOS 的打印功能"拍"成了单页 PDF**；它的文字**不是大模型现场写的，而是模板渲染**；真正有技术含量的只有两处——几何测量（已在前一份报告拆完）和缺陷信号的双通道取证。

这三点决定了本地复刻的难度远低于直觉，也决定了哪些地方**不值得**复刻。

---

## 1. 取证：这份 PDF 是怎么来的

| 取证项 | 值 | 推论 |
|---|---|---|
| 页数 | **1 页** | 非分页排版 |
| 页面尺寸 | **440 × 6894 pt**（宽高比 0.064） | 超长单页，典型的"整页网页打印" |
| `producer` | **`iOS Version 27.3 (Build 24C5037i) Quartz PDFContext`** | 由 **iOS/macOS 的 Core Graphics** 生成 |
| `creator` / `author` / `title` | 全空 | 非专业 PDF 生成器（它们通常会写自己的名字） |
| 字体 | `DMSans-Regular/Bold`、`PlusJakartaSans-ExtraBold`、`SFUIMono-Light`、`HelveticaNeue-Bold` | 全部是 **Web 字体**（DM Sans / Plus Jakarta Sans 来自 Google Fonts） |
| 位图 | 29 张，全部 `ICCBased`、8 bpc | 报告里的卡图是压缩位图，文字和线条是矢量绘制 |
| 文本量 | 5521 字符 / 209 行 | 断行位置与 CSS 折行一致 |

**结论**：链路是 `HTML + CSS → iOS Safari 打印 → Quartz PDFContext → 单页 PDF`。

这条链路有三个可复刻的推论：

1. **他们不需要 PDF 排版引擎**。报告的所有版式都是 CSS 的功劳，导出是免费的。
2. **440 pt 宽 = 移动端布局宽度**。这份 PDF 是手机端截的，说明报告页是响应式的，桌面端会呈现不同版式。
3. **`Download PDF` 按钮极可能只是 `window.print()`**（或在 iOS 上触发分享面板）。这不是猜测的偷懒，而是这条链路最自然的实现。

> 对本地复刻的意义：**用 Playwright 的 `page.pdf()` 打印一个 HTML 页面，就能得到同等质量的报告**，不需要 reportlab / WeasyPrint 这类排版库，更不需要自己算坐标画线。

---

## 2. 信息架构：17 个区块

从上到下逐块拆（括号内为实现方式判断）：

| # | 区块 | 内容 | 实现判断 |
|---|---|---|---|
| 1 | 顶栏 | CGI logo + CardGrade.io + CTA | 静态 |
| 2 | 促销条 | "AI grading in about 60 seconds"，可关闭 | 静态 + JS |
| 3 | **卡砖** | 拉正后的卡图 + 铭牌（CGI / STEPHEN CURRY / **9** MINT） | 卡图经透视校正后套圆角容器 |
| 4 | 正反面切换 | FRONT / BACK | Tab |
| 5 | **总评** | 徽章 + 卡名（可编辑）+ 特号 **9** + MINT | 数据绑定 |
| 6 | **三机构预测** | PSA 9 / BGS 9.5 / CGC 10 | 查表映射 |
| 7 | 免责 + **报告 ID** | `ID 0870F85F` | 数据库主键 |
| 8 | 操作 | Share PDF / Download PDF / Public Page | 打印 / 分享 / 公开链接 |
| 9 | **四维拱形条** | CENTERING · CORNERS · EDGES · SURFACE | 纯装饰（见 §4.2） |
| 10 | **居中详情** | `Centering 10` + GEM 徽章 + 卡图叠红/绿线 + 49/51、45/55 | 复用了前一份报告拆解的边框检测 |
| 11 | **扫描摘要** | CORNERS/EDGES/CENTERING/SURFACE 四面数值 + 尺寸 | 结构化数据 |
| 12 | **CARD MAP** | 卡图上 8 个分区标注（4 角 9.7，4 边 10） | 绝对定位的角标 |
| 13 | **等级证据** | 17 条信号，每条含严重度/位置/类型/来源 | 事件列表 |
| 14 | **等级综述** | 一段解释为何是 9 不是 10 | 模板 |
| 15 | **分区分析** | 四段文字，各带 subgrade 与解释 | 模板 + 缺陷列表拼接 |
| 16 | eBay 图导出 | "grade, subgrades & cert QR baked in" | Canvas 合成 → JPEG |
| 17 | 免责长文 + 营销 CTA | 三段免责、16-Zone、119,000+、$4.99/mo | 静态 |

**结构上的一个巧思**：第 10 块（居中）单独给了一整屏和可视化证据（红线=卡片切割边，绿线=画面边框），而另外三维只在第 13 块里给文字列表。原因是**居中是他们唯一能给出"像素级可验证证据"的维度**——另外三维只能给语义判断。这个取舍本身就是对能力边界的诚实暴露。

---

## 3. 核心数据：这份报告到底测到了什么

### 3.1 扫描摘要（原文数值）

```
CORNERS     F: 4 MINOR          B: 4 MINOR
EDGES       F: 0 DETECTIONS     B: 0 DETECTIONS
CENTERING   F: 49L/51R 45T/55B  B: 59L/41R 49T/51B
SURFACE     F: 1 MINOR          B: 0 DETECTIONS
DIMENSIONS  H: 3.5"  W: 2.5"
```

### 3.2 等级证据（17 条信号）

正面 4 角 × 2 类 = 8 条，背面同理 8 条，表面 1 条，合计 **17**。

每条信号是一个四元组：

```
严重度   位置                          缺陷类型        来源
MINOR    Front · Top left · corner     Corner touches  AI flagged · minor
MINOR    Front · Top left · corner     Whitening       AI flagged · minor
MINOR    Front · Top right · corner    Corner touches  AI flagged · minor
...（共 17 条）
```

注意 `位置` 用了三段式面包屑：**面 · 分区 · 类别**。这是数据结构直接渲染的结果，不是自然语言。

### 3.3 CARD MAP 分区分数

卡图上 8 个角标：**四个角全部 9.7，四条边中点全部 10**，底部一行 `Lowest zone Top Left 9.7`（取第一个最低值）。

由此可反推整套评分的层级：

```
zone_score   → 9.7（角）/ 10（边）      ← 连续值，逐区计算
subgrade     → Corners 9 / Centering 10 / Edges 10 / Surface 10
overall      → min(四个 subgrade) = 9
```

---

## 4. 生成逻辑逆向

### 4.1 评分模型（可解释重建，标注为推断）

把三组数字放在一起，唯一自洽的模型是**"缺陷扣分 → 分区分数 → 保守聚合 → 取短板"**四层：

```
第 1 层  单条缺陷赋权
        MINOR 缺陷 ≈ 0.15 分/条（可从 9.7 反推：一个角 2 条 minor → 10 − 0.3 = 9.7）

第 2 层  分区分数 zone_score
        角区 = 10 − Σ(该区缺陷权重)  → 9.7
        边区 = 10（0 detections）   → 10

第 3 层  维度子分 subgrade
        Centering / Edges / Surface = 10
        Corners = 9      ← 关键：不是 4 个角区取平均（9.7），而是更保守
                            的聚合，把"4 个角都有问题"这件事本身计入惩罚

第 4 层  总评 overall
        overall = min(Centering, Corners, Edges, Surface) = 9
        并输出 Limiting Factor = Corners
```

**最值得注意的一处**：`zone_score = 9.7` 但 `subgrade = 9`。

如果只是"取最低分区"，结果应该是 9.7；如果四舍五入，应该是 10。两者都不是 9。所以 Corners 的子分必然经过了**额外惩罚**——最合理的解释是：单区看 2 条 minor 扣 0.3，但**四个角同时存在缺陷**触发了"系统性/一致性"惩罚，把子分再压一档。

这与 BGS 体系的逻辑同源：子分不是缺陷的算术平均，而是"短板 + 一致性"双重约束。报告里那句 *"conservatively limited the overall grade"* 里的 **conservatively** 不是修辞，是算法描述。

> 这是整个报告里唯一"不可见但可推"的环节。复刻时如果想对齐它的输出，必须显式实现"跨区一致性惩罚"，否则永远差一档。

### 4.2 四维拱形条：一个诚实性缺口

四条拱形色块（CENTERING / CORNERS / EDGES / SURFACE）经 PDF 绘图对象核对：

```python
rect=(33,1346,121,1431)   fill=(0.2039, 0.8275, 0.6000)   # #34D399
rect=(129,1346,216,1431)  fill=(0.2039, 0.8275, 0.6000)
rect=(224,1346,312,1431)  fill=(0.2039, 0.8275, 0.6000)
rect=(320,1346,407,1431)  fill=(0.2039, 0.8275, 0.6000)
```

**四个色块尺寸完全相同、颜色完全相同、都填满**。

也就是说：**CORNERS 实际是 9 分（短板），但它的拱形条与 10 分的三项长得一模一样**。唯一的提示是下面一行黄色小字 `Limiting Factor: Corners`。

这是一个真实的可视化缺陷：图形层不承载分数信息，却给人一种"四项全绿"的暗示。复刻时**不应照搬**——要么让拱形按分数高度填充，要么直接标数字。

### 4.3 文案生成：模板，不是大模型

这是本次拆解里最重要的一条判断，有四条硬证据：

**证据 A：Centering 的描述与实际数值矛盾。**

> "The AI vision analysis confirmed excellent centering, **with no measurable deviations detected** on either the front or back."

但同一份报告的数据是 `49L/51R 45T/55B`，背面 `59L/41R`。49/51 就是 2% 的偏差，59/41 更是 18% 的偏差。**文案说"没有测到可测量的偏差"，数据说有。** 唯一解释是：这段文案是按 `subgrade == 10` 这个**档位**选的模板，根本没读具体数值。

**证据 B：Corners 段落是缺陷列表的直接拼接。**

> "The front top-left corner shows minor corner touches. The front top-left corner shows minor whitening. The front top-right corner shows minor corner touches. ..."

同一个角被同一句式写了两遍（touches 一条、whitening 一条），八个角区、十六条信号，原样铺开，没有任何概括。这是 `for signal in signals: print(f"{zone} shows {defect}.")` 的输出，人是不会这么写的。

**证据 C：句式高度固定。** 四个维度共用一个骨架：

```
{维度}  {subgrade 描述句}  {该维度特有的一句补充}
Centering: "Your card's centering received a subgrade of 10." + "no measurable deviations..."
Edges:     "Your card's edges achieved a subgrade of 10."     + "found no defects or wear..."
Surface:   "The surface of your card received a subgrade of 10." + "While minor scratches...not significant enough..."
```

**证据 D：GRADE SUMMARY 也是填空。**

> "Your card has been assigned an overall grade of **9 (Mint)**. While many aspects of the card presented in Gem Mint condition, the AI vision analysis detected **multiple light touches on the back corners**, which conservatively limited the overall grade from reaching **Gem Mint 10**."

槽位清晰可见：`{总分}` `{档名}` `{限制项描述}` `{更高一档}`。

**结论**：文案层是 **Jinja/Handlebars 式的模板渲染**，输入是结构化 JSON，输出是固定句式。这对本地化是巨大利好——不需要提示词工程，只需要设计好 schema 和模板。

**但这也意味着一个陷阱**：如果你用大模型生成文案，反而会"过于聪明"——它会发现 49/51 与"没有偏差"矛盾并改写，结果与产品原貌不一致。要复刻行为，就该用模板。

### 4.4 双信号体系：AI flagged vs CV measured

报告底部那句话是整套系统的技术自白：

> "No major grading service shows this level of evidence. **'CV measured' signals** come from sub-pixel pixel analysis (fraying, whitening, edge wear) that **can override a clean visual read**."

它明确区分了两类信号来源：

| 来源 | 含义 | 特征 |
|---|---|---|
| `AI flagged` | 视觉模型判定 | 语义级、覆盖全卡、可能幻觉 |
| `CV measured` | 亚像素像素分析 | 可量化、可复算、可推翻 AI |

本样本的 17 条信号全部标为 `AI flagged`，没有一条 `CV measured`。说明这张卡没有触发亚像素通道，或者该通道只在特定条件下启用（如边缘毛边、明显露白）。

**这条设计非常值得学**：它本质上是一个**双通道交叉验证**架构——

```
VLM 提议（哪里可能有问题）  →  CV 裁决（那里到底有多少白色像素/多粗的毛边）
```

VLM 负责"找得到"，CV 负责"说得准"。后者的结论优先级高于前者（"can override"）。这恰好也是本地复刻时压制 VLM 幻觉的标准解法。

### 4.5 "16 分区"的真相

营销文案说 `16-Zone Analysis`，但 CARD MAP 实际只标了 **8 个区**（4 角 + 4 边中点），分数也只有 8 个。

两种可能（无法从单一样本判定）：
1. 内部确实把卡片切成 4×4 = 16 格扫描，但对外只呈现 8 个"语义区"；
2. "16 zone" 是营销口径，与实际呈现不一致。

无论哪种，**用户看到的是 8 个**。复刻时按 8 个做即可对齐观感；若要更细，用 4×4 网格也不会与它的宣传冲突。

---

## 5. 与我们已复刻部分的关系

上一轮逆向的是 CardGrade 的**居中工具**（`/tools/centering`），这一轮拆的是**AI 报告**。两者的关系现在完全清楚了：

```
                ┌──────────────────────────────┐
                │  同一个产品，两条独立链路        │
                └──────────────────────────────┘

  居中工具（免费）                       AI 报告（付费 $4.99/mo）
  ─────────────────                    ─────────────────────────
  纯浏览器                              上传后端
  2 个 Web Worker                       VLM 推理
  Sobel + 行列投影                       CV 亚像素复核
  结果不出本机                           结果存库 + 分配 ID
  无账号                                需要登录
        │                                      │
        └──────────► 共用同一个"红/绿线"可视化 ◄────┘
                     （card edge / artwork edge）
```

报告中第 10 块的卡图上叠了红线和绿线，图例写着 `Red = card edge · Green = artwork edge · pixel-accurate`——这正是我们已经在 `centering_calculator.py` 里实现的内外框检测。**那一部分不需要重做，直接复用。**

需要新建的是另外三维：Corners / Edges / Surface。

---

## 6. 复刻难度评估

| 模块 | 难度 | 依据 | 是否值得自建 |
|---|---|---|---|
| 居中测量 | ★☆☆☆☆ | 已有实现，误差 ±0.2pp | ✅ 已完成 |
| 卡牌识别（认卡） | ★★☆☆☆ | CLIP 检索 + 卡库，或 VLM 直接读卡面文字 | ✅ 值得 |
| 尺寸验证 | ★☆☆☆☆ | 已知卡宽高比，几何反推 | ✅ 顺手做 |
| 角/边/表面缺陷定位 | ★★★☆☆ | 本地 VLM 可做，需控制幻觉 | ✅ 核心价值 |
| 亚像素缺陷量化 | ★★★☆☆ | OpenCV 可做，需标定阈值 | ✅ 差异化 |
| 评分聚合 | ★★☆☆☆ | 规则引擎，需调"一致性惩罚" | ✅ 必须 |
| 机构预测映射 | ★☆☆☆☆ | 查表 + 分位映射 | ✅ 简单 |
| 报告渲染 | ★★☆☆☆ | HTML 模板 + Playwright 打印 | ✅ 简单 |
| 市场估值 | ★★★★☆ | 需真实成交数据源 | ❌ 不建议 |
| 卡库/认证 | ★★★★★ | 需要数据库与授权 | ❌ 不做 |

**结论**：除了市场估值和卡库，其余全部可以在单机上用开源栈做出来。

---

## 7. 三条必须放弃复刻的东西

写方案前先说清楚哪些不该做，否则方案会变成幻想：

1. **市场估值 / ROI**。CardGrade 的定价能力来自成交数据（eBay 成交、拍卖记录），不是算法。没有数据源，做出来就是编数字。
2. **卡牌数据库与认证**。认卡需要覆盖几万张卡的图像库或结构化元数据。个人自用可以用"读卡面文字 + 人工确认"替代。
3. **"119,000+ cards graded" 这类规模效应**。模型的准确率来自标注数据积累，个人无法复制。**接受这一点，方案才是可行的。**

本地方案的目标应当明确为：**给自己一个"送评前的第二双眼睛"，而不是复刻一个商业产品**。
