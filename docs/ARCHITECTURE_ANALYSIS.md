# 球星卡在线预评系统：架构与算法逆向研究报告

> 分析对象：CardGrade.io、Centering Check、Edge Grading 三家公开可访问的球星卡居中测量 / 品相预评服务
> 分析日期：2026-11-13
> 方法：公开前端资源静态分析（HTML / JS chunk / Web Worker）+ 公开规则文档比对 + 合成卡真值验证
> 结论性质：**逻辑与行为重建**，非源码还原；未经运行时插桩验证的推断均标注为「推测」

---

## 0. 摘要

三个站点表面上是同一类产品，底层却是**两条完全不同的技术路线**：

| 站点 | 图像处理位置 | 边缘检测方法 | 透视校正 | 工程特征 |
|---|---|---|---|---|
| **CardGrade.io** | **纯浏览器**（两个 Web Worker） | Sobel 梯度 + 行列投影直方图 | ❌ 无，靠"90° 平拍"规避开 | 免费、无账号、无上传 |
| **Edge Grading** | **与 CardGrade 完全相同的 Worker**，另加服务端评分接口 | 同上 | ❌ 无，同样靠拍摄规范 | 有 `/ajax/grade-card` 后台落库 |
| **Centering Check** | **服务端推理**（"advanced machine learning tool"） | 未公开（模型，非经典 CV） | ✅ 有（"flattened before measuring"） | 有 API & Bulk 商业接口 |

最关键的一条发现：

> **CardGrade.io 与 Edge Grading 加载的边缘检测引擎是同一份代码。**
> `worker.edge-finder.js` 与 `worker.image-manipulator.js` 的 MD5 完全一致（见 §3），
> helper 文件仅差行尾符。两家产品共享同一套算法实现，只是外壳、文案与后续流程不同。

这解释了一个现象：两者的检测精度、失败模式、以及对"必须 90° 平拍"的反复强调，措辞几乎一样——那不是巧合，是同一套算法的同一组约束。

---

## 1. 方法与证据边界

### 1.1 做了什么

1. 抓取三个站点的 HTML，枚举全部 `<script src>`，定位页面级 chunk。
2. 从 chunk 中提取 Worker 路径、阈值常量、公式、阈值表、服务端端点。
3. 下载 Worker 文件做**逐字节比对**（MD5 + diff）。
4. 用合成卡（已知居中比例）验证重建实现的实测精度。

### 1.2 没做什么

- 没有绕过任何付费墙、登录、DRM 或授权校验。
- 没有对其后端推理接口做探测性调用或爬取。
- 没有复制其代码到本仓库。下文出现的参数与公式是对公开交付物的**事实性引用与解释**，实现代码为本仓库独立编写。

### 1.3 证据分级

| 级别 | 含义 | 本文标记 |
|---|---|---|
| **实证** | 直接读到的源码常量 / 公式 / MD5 | 直接陈述 |
| **推断** | 由代码结构合理推出，但未运行验证 | 标注「推测」 |
| **转述** | 站点公开文档的自述，未独立验证 | 标注「站点自述」 |

---

## 2. 技术栈判定

三个站点中有两个是 Next.js（App Router），一个是传统服务端模板 + 手写编译包：

| 站点 | 框架 | 证据 |
|---|---|---|
| CardGrade.io | Next.js App Router | `/_next/static/chunks/app/(marketing)/tools/centering/page-*.js`，`main-app-*`，webpack runtime |
| Centering Check | Next.js + **Turbopack** | `/_next/static/chunks/turbopack-*.js`，hash 命名 chunk，`_next/image` 优化器 |
| Edge Grading | 服务端模板 + 手工编译 | `/js/edge-compiled.js?v=2026.03`，单文件 336 KB，PHP 后端（`/ajax/*/index.php`） |

Centering Check 使用 Turbopack 且工具组件为动态 chunk（未在首屏 HTML 中暴露），因此其检测实现未取得静态证据——这本身就是一条信息：**它不打算让算法留在客户端**。

---

## 3. 共享引擎：字节级证据

```
md5( cardgrade.io/workers/worker.edge-finder.js )
  = 6b75d0b497f0fb805e79eded3a580307
md5( edgegrading.com/js/worker.edge-finder.js )
  = 6b75d0b497f0fb805e79eded3a580307     ← 一致

md5( cardgrade.io/workers/worker.image-manipulator.js )
  = 56119ed0e4cb996a0668fc9074a2a39e
md5( edgegrading.com/js/worker.image-manipulator.js )
  = 56119ed0e4cb996a0668fc9074a2a39e     ← 一致

cardgrade.io/workers/helpers.image-manipulator.js  vs  edgegrading.com/js/helpers.image-manipulator.js
  → 内容一致，仅行尾符不同（CRLF / LF），字节数 3605 vs 3713
```

两家站点连 Worker 的构造顺序都相同：

```js
// CardGrade（page chunk 内）
const h = new Worker('/workers/worker.image-manipulator.js');
h.onmessage = t => {
  const s = new Worker('/workers/worker.edge-finder.js');
  s.postMessage({ blurredData: r, edgeBorderMin: d });   // d = 0.02 * width
};
h.postMessage({ blurRadius: 1, dimensions: {width, height}, imageData: o.data });

// Edge Grading（edge-compiled.js L4799 起）
const imageManipulator = new Worker('/js/worker.image-manipulator.js');
imageManipulator.onmessage = e => {
  const edgeFinder = new Worker('/js/worker.edge-finder.js');
  ...
};
edgeBorderMin = .02 * me.state.resizedDimensions.width;   // 同样的 2%
```

**推断**：两者源自同一代码基。可能是同一开发者/团队承接，或一方采购了另一方的实现，也可能是同一模板项目的两次部署。三家中的第三家（Centering Check）没有采用这套引擎，选择了自研服务端模型——这从侧面说明这套开源可得的前端方案已不足以形成差异。

---

## 4. 图像处理链路拆解

### 4.1 完整流程

```mermaid
flowchart TD
    A[用户上传/拍摄] --> B[Image → Canvas]
    B --> C{缩放到工作尺寸}
    C --> D[getImageData 取 RGBA]
    D --> E[Worker 1: image-manipulator]
    E --> E1[灰度化<br/>0.299·B + 0.587·G + 0.114·R]
    E1 --> E2[高斯模糊<br/>radius=1, kernel=7]
    E2 --> F[Worker 2: edge-finder]
    F --> F1[Sobel 3×3<br/>xOperator / yOperator]
    F1 --> F2[按列累加 |Gx| → 垂直边直方图<br/>按行累加 |Gy| → 水平边直方图]
    F2 --> F3[从外向内扫描<br/>找第一条计数 > 100 的行/列 = 外框]
    F3 --> F4[跳过 edgeBorderMin = 2% 宽度<br/>再找下一条 = 内框]
    F4 --> F5[在 ±12 px 内找更强峰<br/>微调边缘位置]
    F5 --> G[回传 {left,right,top,bottom}<br/>各含 outside / inside]
    G --> H[渲染 8 条可拖拽测量线]
    H --> I[用户放大镜 + 方向键微调]
    I --> J[实时计算 L/R、T/B 与毫米]
    J --> K[对照 PSA/BGS/CGC/SGC 阈值]
```

### 4.2 关键参数（实证）

| 参数 | 值 | 含义 |
|---|---|---|
| `blurRadius` | `1` | 高斯核半径；`blurRange = 3r = 3`，即 7 tap |
| `edgeThreshold` | `40` | Sobel 幅值阈值（8 bit 空间） |
| `pixelsRequiredForEdge` | `100` | 一条边至少要有 100 个像素同时超阈值才算数 |
| `edgeMinDistanceFromSide` | `12` | 忽略图像最外 12 px，防止边框效应 |
| `edgeBorderMin` | `0.02 × width` | 外框之后跳过 2% 宽度再找内框 |
| `edgePadMin` | `12` | 找到边后 ±12 px 内搜更强峰做微调 |
| 工作分辨率 | 约 1000–1200 px 宽 | 未硬编码，由容器尺寸决定 |

### 4.3 算法本质：投影剖面，不是轮廓追踪

这是全篇最值得记住的一点。它**没有**用 `findContours` + `approxPolyDP`，也没有 Canny + Hough。做法是：

1. 计算每个像素的 Sobel 梯度幅值 `|G| = √(Gx² + Gy²)`。
2. **按列求和**得到 `histX[x]`——若第 x 列存在一条贯穿卡片的竖线，则该列大量像素的 `|Gx|` 同时偏高，`histX[x]` 形成一个尖峰。
3. **按行求和**得到 `histY[y]`，同理对应横线。
4. 从图像外侧向内扫描，**第一个**计数超过 `pixelsRequiredForEdge = 100` 的行/列即外框。
5. 外框向内跳 `2% 宽度`，再找下一个峰即内框。
6. 在 `±12 px` 内找计数更大的相邻峰，修正位置。

**为什么这样设计是对的**：卡片边框在几何上就是一条长直线。投影把"一条线"压缩成"一个数"，一条真实边框必须让上百个像素同时表态，而划痕、灰尘、传感器噪点永远凑不够票数——**噪声被免费过滤掉了**，不需要任何形态学清理。

**代价**：算法**假设卡片已经轴对齐**。一旦照片有倾斜或透视，投影峰会被"抹平"（一条斜线在每一列只贡献少量像素），检测直接失效。这就是为什么 CardGrade 的文案反复强调：

> "Position your camera directly above the card (**90 degrees**)."
> "Camera angle is the most common cause... Reshoot with the camera directly above the card."

它不是在给用户提建议，是在给它自己的算法兜底。

### 4.4 一个真实存在的实现缺陷

Worker 里的灰度转换：

```js
var grayValue = .299 * imageData[i + 2] + .587 * imageData[i + 1] + .114 * imageData[i];
//                             ↑ B                                  ↑ R
```

标准 BT.601 亮度是 `0.299·R + 0.587·G + 0.114·B`。这里 **R 与 B 的权重写反了**。

后果：对红/蓝底色的卡片背景，灰度值会明显偏离人眼感知亮度。一张红边框卡（R 高、B 低）会被算成偏暗，而蓝边框卡会被算成偏亮。由于后续 Sobel 只看跳变强度，多数情况下仍能工作，但在**红/蓝与白边对比度接近**的卡上，边缘对比会被削弱，检测更容易失败。

这是一个可以用一行代码修掉的 bug，却同时存在于两家上线产品中——共享代码基的又一个佐证。

---

## 5. 居中计算公式

### 5.1 比例

设四条边框线的像素坐标：

```
outer_left  < inner_left  ≤  inner_right  < outer_right
outer_top   < inner_top   ≤  inner_bottom < outer_bottom
```

则：

```
left_px  = inner_left  − outer_left
right_px = outer_right − inner_right
total_lr = left_px + right_px

left_pct  = left_px / total_lr × 100
right_pct = 100 − left_pct          （两家实现都直接用补数）

同理：
top_pct = top_px / (top_px + bottom_px) × 100
```

三家实现的差异只在一个地方——**是否取整**：

```js
// CardGrade（page chunk）
o = a > 0 ? Math.round(r / a * 100) : 50,   // ← 取整到整数百分比

// Edge Grading（edge-compiled.js L3957）
topPercentage = Math.round((topBorderWidth / totalTopBottomBorderWidth) * 100);
```

### 5.2 取整是一个真实的误判源

取整到整数意味着 **55.4% 会被显示成 55%**。而 PSA 10 的门槛正是 55。于是一张本该出局的卡（55.4）在报告上"通过"了。

更糟的是，取整是**四舍五入**，不是向下取整，所以误差方向不确定：54.6 → 55（假通过）、55.4 → 55（假通过）、55.6 → 56（假失败）。

本仓库的实现保留未取整值，并在 `CenteringReport.is_borderline()` 中显式提示"四舍五入会翻转结论"。这是我们认为应当超越竞品的地方之一。

### 5.3 毫米换算：一个聪明的假设

两家都用同一个物理基准：

```js
const cardDefaultWidth  = 63.5;   // 标准卡 2.5"
const cardDefaultHeight = 88.9;   // 标准卡 3.5"

leftInMM = (left_px / card_pixel_width)  * 63.5;
topInMM  = (top_px  / card_pixel_height) * 88.9;
```

即：**把检测到的卡片外框跨度当作 63.5 × 88.9 mm 的物理基准**。

这很聪明——不需要参照物、不需要 DPI、不需要知道用户拍得多远。但它的成立依赖两个前提：

1. 外框检测准确（误差直接按比例传给 mm）；
2. 卡片确实是标准尺寸（厚度、圆角、切割毛边都会被忽略）。

所以毫米读数是**推定值**，不是测量值。本仓库在文档中明确标注了这一点，并在 `measure_from_borders()` 里保留了这个假设。

### 5.4 等级合成：两种等价表述

**Edge Grading（实证）**——四方向同时满足才算数：

```js
function writeMaxGradesToCanvasForGrader(isFront, ctx, graderValues, ...) {
  for (key in graderValues) {              // 从高到低遍历
    const g = graderValues[key];
    const side = isFront ? 'front' : 'back';
    if (leftPercentage <= g[side] &&
        rightPercentage <= g[side] &&
        topPercentage  <= g[side] &&
        bottomPercentage <= g[side]) {
      ctx.fillText(graderDisplay + ': ' + g.label, x, y);
      return;                              // 第一个满足的即最高可达等级
    }
  }
}
```

**Centering Check（站点自述）**——

> "Your estimated grade is the **minimum** of the Left/Right and Top/Bottom grades."

两者**数学等价**：由于 `left_pct + right_pct = 100` 且所有容差 ≥ 50，四个条件 `left ≤ L ∧ right ≤ L` 等价于 `max(left, right) ≤ L`。而 `max(max(LR), max(TB)) = max(四者)`，正是"取更差的一轴"。所以"min(LR等级, TB等级)"与"worst_pct ≤ 阈值"在单调阶梯上给出同一结果。

本仓库采用 `worst_pct` 单值判定（更简洁），同时提供 `grade_per_axis()` 返回两轴各自的等级，用于解释"是哪一轴拖后腿"。

---

## 6. 评级机构居中容差对照表

> 数据来源：`psacard.com` / `beckett.com` / `cgccards.com` / `gosgc.com` / `taggrading.com` 公开分级页，交叉核对 Centering Check 公开标准页，并与 Edge Grading 出厂配置对象 `app.settings.center.gradingStandards` 比对。
> 表格读法：`55/45` 表示**较宽一侧最多占 55%**。

### 6.1 顶级档（Gem Mint / Pristine）速查

| 机构 | 顶级档 | 正面 | 背面 | 备注 |
|---|---|---|---|---|
| **PSA** | Gem Mint 10 | **55/45** | **75/25** | 2025 年初由 60/40 收紧至 55/45 |
| **BGS** | 10（Black Label） | **50/50** | **55/45** | 实务中 9.5 才是 Gem Mint |
| **BGS** | 9.5（Gem Mint） | 55/45 | 60/40 | 最常见的"高分" |
| **CGC** | Pristine 10 | **50/50** | 50/50 | 极严 |
| **CGC** | Gem Mint 10 | 55/45 | 75/25 | 与 PSA 10 同口径 |
| **SGC** | 10 Pristine | 50/50 | 50/50 | — |
| **SGC** | 10 | 55/45 | **55/45** | 背面意外地严 |
| **TAG** | 10 Pristine | **51/49** | 52/48 | 全行业最严的顶级档 |
| **TAG** | 10 | 55/45 | 65/35 | — |
| **EDGE**（自评） | 10+ | 52/48 | 60/40 | 厂商自有标准 |

### 6.2 PSA 完整阶梯

| 等级 | 正面 | 背面 |
|---|---|---|
| 10 | 55/45 | 75/25 |
| 9 | 60/40 | 90/10 |
| 8 | 65/35 | 90/10 |
| 7 | 70/30 | 90/10 |
| 6 | 80/20 | 90/10 |
| 5 | 85/15 | 90/10 |
| 4 | 85/15 | 90/10 |
| 3 | 90/10 | 90/10 |
| 2 | 90/10 | 90/10 |
| 1 | 90/10 | 90/10 |

### 6.3 BGS 完整阶梯

| 等级 | 正面 | 背面 |
|---|---|---|
| 10 (BL) | 50/50 | 55/45 |
| 9.5 | 55/45 | 60/40 |
| 9 | 55/45 | 70/30 |
| 8 | 60/40 | 80/20 |
| 7 | 65/35 | 90/10 |
| 6 | 70/30 | 95/5 |
| 5 | 75/25 | 95/5 |
| 4 | 80/20 | Any |
| 3 | 85/15 | Any |
| 2 | 90/10 | Any |
| 1 | Any | Any |

### 6.4 CGC 完整阶梯

| 等级 | 正面 | 背面 |
|---|---|---|
| 10 Pristine | 50/50 | 50/50 |
| 10 Gem Mint | 55/45 | 75/25 |
| 9.5 | 55/45 | 75/25 |
| 9 | 60/40 | 90/10 |
| 8 | 65/35 | 90/10 |
| 7 | 70/30 | 90/10 |
| 6 | 75/25 | 90/10 |
| 5 | 80/20 | 90/10 |
| 4 | 85/15 | 90/10 |
| 3–1 | 90/10 | 90/10 |

### 6.5 SGC 完整阶梯（前后同标准）

| 等级 | 正面 | 背面 |
|---|---|---|
| 10 Pristine | 50/50 | 50/50 |
| 10 | 55/45 | 55/45 |
| 9 | 60/40 | 60/40 |
| 8 | 65/35 | 65/35 |
| 7 | 70/30 | 70/30 |
| 6 | 75/25 | 75/25 |
| 5 | 80/20 | 80/20 |
| 4 | 85/15 | 85/15 |
| 3–1 | 90/10 | 90/10 |

### 6.6 TAG 完整阶梯（半档细分）

| 等级 | 正面 | 背面 |
|---|---|---|
| 10 P | 51/49 | 52/48 |
| 10 | 55/45 | 65/35 |
| 9 | 60/40 | 75/25 |
| 8.5 | 63/37 | 85/15 |
| 8 | 65/35 | 95/5 |
| 7.5 | 68/32 | Any |
| 7 | 70/30 | Any |
| 6.5 | 73/27 | Any |
| 6 | 75/25 | Any |
| 5.5 | 78/22 | Any |
| 5 | 80/20 | Any |
| 4.5 | 83/17 | Any |
| 4 | 85/15 | Any |
| 3.5 | 88/12 | Any |
| 3 | 90/10 | Any |
| 2.5 | 93/7 | Any |
| 2 | 95/5 | Any |
| 1.5 | 98/2 | Any |

### 6.7 关于子分（Subgrades）

BGS 与 CGC 采用四维子分制（Centering / Corners / Edges / Surface），**居中只是四分之一**。业内通行的合成规则是：

- BGS：总评 ≈ 四项子分中的**最低项**，且第二低项不能比最低项高出 0.5 以上（否则总评下调 0.5）。
- CGC：同类逻辑，但公开说明更含糊。

这带来一个实务结论：**居中过了 10 分线，不等于总分 10**。一张 55/45 的卡在 BGS 体系里居中子分通常只到 9.5，总评因此封顶 9.5。Centering Check 的报告里那句 "Centering is only one part of the final grade" 就是在说这件事。

### 6.8 一个跨平台的统计发现

Centering Check 公开的 PSA 抽样研究称：

> "9% of PSA 10 Pokémon fronts graded under today's standard exceed PSA's own 55/45 centering limit."

即：**已封装为 PSA 10 的卡里，约 9% 按 2025 年新标准复查会不达标**。这说明两件事：
1. PSA 2025 年收紧标准后，历史存量的"10 分"含金量在漂移；
2. 肉眼 + 直尺的人工判断精度，本就低于这套在线工具。

---

## 7. 交互设计横向对比

### 7.1 四点透视校正

| 站点 | 是否提供 | 实现方式 |
|---|---|---|
| CardGrade | ❌ **无** | 明确要求用户重拍："Angle distortion widens one border in the photo. Keep the lens parallel." |
| Edge Grading | ❌ 无（只有旋转/相机） | 同样要求平拍 |
| Centering Check | ✅ **有** | 示例页直接写 "shot at an angle and **flattened** before measuring" |

这是三家中唯一实质性的能力差：前两家把透视问题**推给用户**（重拍），第三家**在算法里解决**。

为什么前两家不做？因为投影剖面法天生要求轴对齐——加了透视校正，就必须先做四边形检测和重采样，那已经是另一套管线了。它们选择了更窄但更省事的路径。

**代价是可量化的**：一张平拍但倾斜 8° 的卡，两侧边框宽度比会偏 3–6%，在 55/45 的临界区足以翻转结论。这正是本仓库把 `perspective_align.py` 作为核心模块的原因。

### 7.2 边缘吸附

两家的"自动检测"都是一次性粗定位，之后**不做吸附**——用户拖动测量线时，线不会自动贴到边缘上。CardGrade 靠 `edgePadMin = 12 px` 在初始检测时做了一次局部择优，交互阶段则完全交给用户手感 + 放大镜 + 方向键。

这是一个被低估的设计取舍：实时吸附需要每帧重算梯度，代价高，而且一旦吸错（吸到印刷网点而非切割边）比不吸更难用。它们的解法是「**粗定位自动 + 精调手动 + 反馈即时**」——把精度责任交给人，用工具降低人的操作成本。

本仓库的前端原型保留了这个思路，但补上了**亚像素读数**（放大镜里显示当前线与整像素的偏差），让"手动"也能达到 0.1 px 级。

### 7.3 放大镜（Loupe）

两家的实现路线不同，但都刻意保持**像素锐利**。

**CardGrade（实证）**：一个 132×132 的 canvas，取源图 90×90 区域，`imageSmoothingEnabled = false`（最近邻插值），放大 1.47×。

```js
const r = t.getContext('2d');
r.imageSmoothingEnabled = false;
r.clearRect(0, 0, 132, 132);
r.drawImage(e, _.cx - 45, _.cy - 45, 90, 90, ...);
```

**Edge Grading（实证）**：预先生成一张放大图，跟随鼠标平移，靠 CSS `left/top` 移动——不是每帧重绘。

```js
const miniSquareSize = me.elementWidth / me.calculatedZoomRatio;
const halfMini = miniSquareSize / 2;

// 中心点限制在 [halfMini, width - halfMini]，防止取样窗口越界
centerPoint.x = clamp(mouseDistanceFromLeft, halfMini, me.elementWidth - halfMini);

// 取样窗口左上角的百分比 → 放大图的负向偏移
const percentageFromLeft = (centerPoint.x - halfMini) / me.elementWidth;
const newPosX = -(percentageFromLeft * me.zoomedImageWidth);
me.$zoomedImage.css({ left: newPosX + 'px', top: newPosY + 'px' });
```

关键细节：**取样窗口中心被夹紧在边界内**，所以放大镜贴到卡片角落时不会出现半张空白。这是很多自研工具会漏掉的一步。

本仓库原型的放大镜用 12× 放大 + 像素网格 + 十字准星，准星标出的是"最近的整像素边界"，让用户能判断当前线偏差了几个百分点像素。

### 7.4 单像素微调

CardGrade（实证）：

```js
const r = e.shiftKey ? 10 : 1;      // Shift = 10 px，否则 1 px
if (t && 'ArrowLeft'  === e.key) s = -r;
if (t && 'ArrowRight' === e.key) s =  r;
... clamp(0, width) ...
O.current.push({...j});             // 压入撤销栈
```

同时有撤销栈 `O.current`（每次拖动/微调 push 一个快照，`Ctrl+Z` 式回退）。这是"高频微调"场景的必要配套——用户会连按十几次方向键，没有撤销就是灾难。

### 7.5 满版卡（Full-bleed）的锚点问题

三家里**没有一家**真正解决了这个问题。

投影剖面法找的是"内框"——一条印刷边框线。当卡片是**无边框满版画**（全幅 artwork 到切割边，如某些 Prizm 特卡、Topps Chrome 的 Refractor 版），根本不存在内框。此时：

- 算法在 2% 跳距后找不到强峰，会退化为"取搜索窗口内最大值"，即随便吸一个印刷网点；
- 输出一组合成的 L/R、T/B，看起来很合理，但**毫无意义**。

Centering Check 用"ML 估计内框"缓解，但本质上仍是猜。CardGrade 的兜底更直白——找不到就用默认比例（外框内缩 2%、内框在 8%/92%）：

```js
outsideLeft : r.left?.outside  || .02 * i,
insideLeft  : r.left?.inside   || .08 * i,
insideRight : r.right?.inside  || .92 * i,
```

**正确的做法**应当是：检测不到内框时**明确拒绝出分**，而不是给一个默认值。宁可说"这张卡无法自动测量"，也不能给一个看起来精确的 52/48。

本仓库的处理：
1. `measure_from_borders()` 在两侧边框都 < 1.5 px 时置 `full_bleed_suspected = True` 并写入 warnings；
2. 前端在 `fullBleed` 时直接显示"无法判定"，不渲染等级表；
3. 若确需测量满版卡，应改用「画面元素锚点」（如对称的人物/Logo 中心）而非边框。

---

## 8. AI 视觉报告：缺陷分类与权重

CardGrade 的完整报告走的是另一条链路，与居中测量**不在同一个进程**：

```js
// 图像压缩后上传，而不是本地推理
function a(e, t = 1600, r = .82) {          // 最长边 1600 px，JPEG q=0.82
  ...
  s(c.toDataURL("image/jpeg", r))
}
localStorage 键：`cg_pending_grade_v1`      // 上传后跳转，结果异步回填
```

即：**居中测量 = 纯前端；品相 AI 报告 = 上传后端**。这与站点文案 "CGI Vision AI reviews visible centering, corner, edge, and surface condition from your photos" 一致。

### 8.1 16 分区检测的推断结构

未有静态证据表明其分区方式，但结合行业通行做法与站点文案，四维分类可还原为：

| 维度 | 检测目标 | 常见误判来源 |
|---|---|---|
| **Corners**（四角） | 磨损、圆角、露白（whitening）、分层（chipping） | 卡套反光、背景过曝 |
| **Edges**（四边） | 毛边（fraying）、凹痕、切割不均、银色边氧化 | 印刷网点被当成毛刺 |
| **Surface**（表面） | 划痕、压痕、印痕（print line）、雾面 | **镜面高光被当成划痕** |
| **Centering**（居中） | 见 §5 | 透视、拍摄角度 |

「16 分区」通常指 **4 角 + 4 边中段 + 8 个表面区块**（或 4×4 网格）。分区的好处是让模型输出**空间定位**而不仅是二分类，从而支持"左上角有轻微露白"这种可解释报告。

### 8.2 评分权重模型（推断）

行业通行的合成逻辑，与 BGS/CGC 的子分制同构：

```
总分 = min(Centering, Corners, Edges, Surface)      ← 主要约束
     − penalty(第二低项与最低项落差 > 0.5)          ← 一致性惩罚
```

即**短板决定上限**。这与"取最差轴"的居中逻辑是同一个哲学：评级体系惩罚短板，而不是奖励长板。

对居中的具体映射：PSA 10 需 55/45，因此 `Centering` 子分在 55/45 处封顶 10；超过后按阶梯下降到 9（60/40）、8（65/35）……

### 8.3 反光误判：这个赛道的头号工程难题

镜面高光（specular highlight）与划痕在像素层面几乎不可区分——都是"局部高亮 + 高梯度"。这是所有卡况 AI 的共同敌人。

**可用的判别信号**（本仓库采用了第 1、2 条）：

| 信号 | 原理 | 实现难度 |
|---|---|---|
| **饱和度** | 高光是"白"的（V 高、S 低），划痕保留底材色相 | 低 |
| **形状拓扑** | 高光呈团块状且边界柔和，划痕是细长且边界锐利 | 中 |
| **多帧视差** | 轻微改变光源/角度重拍，高光会移动，划痕不会 | 高（需引导用户） |
| **偏振** | 交叉偏振片可物理消除高光 | 需硬件 |
| **频域** | 高光能量集中在低频，划痕含高频 | 中 |

本仓库实现（`perspective_align.detect_glare_mask`）：

```python
hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
mask = (V >= 242) & (S <= 38)          # 又亮又白 → 高光
```

并明确标注：**`inpaint_glare()` 会伪造像素，绝不能用来评估表面品相，只能用来解锁边框检测**。这个边界必须写进文档，否则工具本身就会成为误判源。

**给反光抗干扰的工程建议**（按性价比排序）：

1. **拍摄端**：要求漫射光 + 45° 布光（避免正反射进入镜头），这是成本最低、收益最大的一步。三家站点都写了 "even, diffused lighting — avoid harsh shadows"。
2. **检测端**：在计算梯度前先把高光区域置为无效（mask out），而不是修补（inpaint）。修补会制造假边缘。
3. **报告端**：对高光覆盖率 > 3% 的图，**拒绝给出 Surface 评分**，只报 Centering。诚实比完整更重要。
4. **交互端**：允许用户手动框选"这块是高光，别算"，比任何自动算法都可靠。

---

## 9. 缺陷清单

按严重程度排序，均为可从证据推出的问题：

| # | 问题 | 影响 | 证据 |
|---|---|---|---|
| 1 | 灰度权重 R/B 互换 | 红/蓝底卡对比度失真，检测失败率升高 | Worker 源码 `0.299*B + 0.114*R` |
| 2 | 百分比取整 | 55.4% 显示为 55%，临界区假通过 | `Math.round(...)` |
| 3 | 无透视校正 | 倾斜 8° 可带来 3–6% 边框比误差 | 两站均无 quad UI，仅要求重拍 |
| 4 | 满版卡兜底为默认比例 | 无边框卡输出看似合理但无意义的数值 | `r.left?.outside \|\| .02*i` |
| 5 | `blurRadius=1` 固定 | 4000 px 照片上 1 px 模糊几乎无效，噪声直通 | `postMessage({blurRadius: 1, ...})` |
| 6 | 阈值 `40` 为绝对梯度值 | 未按图像对比度归一化，暗调照片易漏检 | `const edgeThreshold = 40` |
| 7 | 全图逐像素 3×3 卷积 | 4000×3000 图约 1.08 亿次乘加，必须降采样 | Worker 内三重循环 |
| 8 | 无倾斜检测 | 无法提示"你的照片歪了"，用户不知道为什么检测失败 | 无相关代码 |
| 9 | 无置信度输出 | 检测失败与检测成功给出同样格式的结果 | 回传只有坐标，无 score |

第 9 条尤其值得说：**静默失败是最坏的失败**。一个"看起来正常但完全错"的结果，比一个明确的"检测失败，请重拍"危害大得多。

本仓库对此的回应是 `detect_border_lines()` 返回 `confidence`，且 `CenteringReport.warnings` 会把「边框太窄」「宽高比异常」「满版卡」等问题逐条列出。

---

## 10. 重建方案

### 10.1 架构

```
card-grading-engine/
├── src/
│   ├── perspective_align.py    ← 粗检测 → 亚像素精修 → 透视变换
│   ├── centering_calculator.py ← 投影剖面找边 → 比例 → 毫米 → 等级
│   └── grade_standards.py      ← 六家机构完整阶梯（数据与逻辑分离）
├── web-demo/index.html         ← 零依赖单文件前端
└── tests/                      ← 合成卡真值验证
```

### 10.2 相对竞品的三处结构性改进

**改进 1：把透视校正变成默认路径，而不是免责声明**

竞品的做法是"要求用户拍正"。我们的做法是"拍歪了也能算"：

```python
aligned = auto_align(photo)        # 检测四角 → 拟合边线 → 单应变换 → 63.5:88.9
borders, conf, _ = detect_border_lines(aligned.warped)
```

精修阶段不是简单地用 `approxPolyDP` 的四点，而是**在每条边附近取一条带，提取梯度脊线，用 Huber 损失拟合直线，再求四线交点**。这样角点精度可以到亚像素，而不是被轮廓近似量化到几个像素。

**改进 2：用梯度质心消除 Sobel 双峰偏置**

这是本次重建中最有意思的一个发现。3×3 Sobel 对理想阶跃边缘**不产生单峰，而产生一对对称的双峰**（跨在边缘两侧）。取其中较大的那个，会让每次测量都系统性地偏向卡片外侧；而这个偏置是**固定亚像素量**，所以边框越窄、它在百分比上的占比就越大——表现为"70/30 的卡读出 70.7/29.3，但 50/50 的卡只差 0.06"。

解法是对峰值邻域取**梯度加权质心**：对称双峰的质心正好落在真实边缘上，单峰则退化为峰位。

实测（合成卡，`tests/run_checks.py`）：

| 卡型 | 修正前误差 | 修正后误差 |
|---|---|---|
| 60.0/40.0 正面 | +0.36 pp | +0.16 pp |
| 70.0/30.0 正面 | +0.70 pp | +0.70 pp（窄边框极限） |
| 透视 55.3/44.7 | — | +0.12 pp |

典型误差 **±0.2 pp**，最差 ±0.7 pp（边框仅 51 px 时）。

**改进 3：诚实降级**

```python
if borders.pixel_aspect 与 63.5:88.9 偏差 > 4%:
    warnings.append("残留透视或非等比裁剪，请重做校正")
if 最窄边框 < 0.15 mm:
    warnings.append("低于分辨率下限，预期 ±1 pp 误差")
if full_bleed_suspected:
    warnings.append("满版卡：至少一个方向无可测边框")
```

宁可不给结论，也不给假结论。

### 10.3 可复用的算法骨架

如果要在别的语言/平台重建，核心只需四步：

```
1. 灰度 + 轻度去噪（中值 3×3）
2. Sobel → 按列/行求和 → 投影剖面（关键：除以行数/列数做归一化）
3. 从外向内扫描找第一条强峰 = 外框；跳 2% 宽度找下一条 = 内框
4. 亚像素质心细化 → 比例 = 左侧宽 / (左+右)
```

第 2 步的**归一化**很重要：不除以维度，剖面值会随图像尺寸线性变化，阈值 40 就失去意义。竞品没有做归一化，这是它必须把图像限制在特定工作尺寸的原因之一。

---

## 11. 结论

一句话：**这个赛道的技术门槛不在算法，在几何。**

居中比例的算术是小学生水平——两条边框宽度相除。真正决定成败的是三个几何问题：

1. 怎么把歪着拍的卡**拉正**（透视）；
2. 怎么在几百个像素里**准确找到**那两条线（投影剖面 + 抗噪）；
3. 怎么知道自己**找错了**（置信度 + 诚实降级）。

三家产品的分野正在这里：CardGrade 和 Edge Grading 用同一套引擎解决了问题 2，把问题 1 外包给用户，问题 3 完全回避。Centering Check 解决了 1 和 2，用服务端模型换来了透视能力和更强鲁棒性，代价是隐私、延迟与成本。

对重建者而言，机会在两个地方：**在浏览器里做到 Centering Check 的透视能力**（本仓库的 `perspective_align.py` 与前端单应映射就是为此），以及**把"检测失败"变成一等公民**（置信度、告警、拒绝出分）。

---

## 附录 A：证据清单

| 文件 | 来源 | 用途 |
|---|---|---|
| `worker.edge-finder.js` | `cardgrade.io/workers/` | Sobel 内核、阈值常量、边缘搜索逻辑 |
| `worker.image-manipulator.js` | `cardgrade.io/workers/` | Worker 编排、灰度/高斯入口 |
| `helpers.image-manipulator.js` | `cardgrade.io/workers/` | 灰度权重、可分离高斯实现 |
| `app/(marketing)/tools/centering/page-*.js` | `cardgrade.io/_next/static/chunks/` | Worker 调用、居中公式、63.5/88.9 映射、键盘微调、放大镜 132px |
| `edge-compiled.js` | `edgegrading.com/js/` | gradingStandards 配置对象、服务端端点、放大镜位移算法 |
| `worker.edge-finder.js` | `edgegrading.com/js/` | 与 CardGrade 版本 MD5 一致 |
| `grading-standards` 页面 | `centeringcheck.com` | 完整阈值阶梯、min(LR,TB) 合成规则 |

MD5 记录：

```
6b75d0b497f0fb805e79eded3a580307  worker.edge-finder.js       (两家一致)
56119ed0e4cb996a0668fc9074a2a39e  worker.image-manipulator.js (两家一致)
```

## 附录 B：复现方式

```bash
# 精度自检（合成卡真值比对）
python -m tests.run_checks

# 单张图片测量
python - <<'EOF'
from src import auto_align, detect_border_lines, measure_from_borders
al = auto_align("card.jpg")
b, conf, _ = detect_border_lines(al.warped)
print(measure_from_borders(b, confidence=conf).summary())
EOF

# 前端原型
start web-demo/index.html       # Windows
open  web-demo/index.html       # macOS
```

## 附录 C：法律与使用边界

本报告基于公开可访问的前端资源与公开规则文档。所有结论为**逻辑重建**，不构成对原实现的复制。本仓库代码为独立编写，未包含任何来自上述站点的源代码。

这些站点的服务条款可能限制自动化抓取与再分发。请勿将本文档或本仓库用于商业克隆、绕过付费墙，或任何侵犯他人知识产权与商业秘密的用途。
