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
- **无网络调用**：没有模型下载，没有 API key，图片不出本机。

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
│   └── ARCHITECTURE_ANALYSIS.md   逆向研究报告
├── src/
│   ├── perspective_align.py       透视校正：四角检测 / 精修 / 变换 / 去反光
│   ├── centering_calculator.py    投影剖面找边 / 居中计算 / 等级映射
│   └── grade_standards.py         六家阈值阶梯（数据与逻辑分离）
├── web-demo/
│   └── index.html                 零依赖单文件前端
├── tests/
│   ├── synthetic.py               合成卡生成（已知居中比例）
│   └── run_checks.py              精度自检
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
