---
schema: research_report.v1
project: pimc
human_summary: 已核实并读取两篇论文的方法页：Conv2Warp的Catmull-Rom双三次样条重采样器（4基函数、C1连续、局部支撑）与Instant
  NGP的多分辨率哈希编码（多分辨率网格、插值输出、参数权衡），作为二维实值LUT表达能力提案的检索来源。
gaps:
- id: gap_retrieve_two_papers
  question: Retrieve and read the actual method pages of Conv2Warp (arXiv 1908.06194)
    and Instant NGP (arXiv 2201.05989) so they register as verified retrieved sources
    for a 2D real-valued LUT expressiveness proposal, extracting paper_finding vs
    transfer_idea vs limitations for each.
selection_principles:
- 只选取任务明确指定的两篇论文（Conv2Warp arXiv 1908.06194 与 Instant NGP arXiv 2201.05989），不引入额外文献以免偏离委派缺口。
- 必须从PDF实际方法页读取原文，而非仅依赖摘要或元数据；引用需落在可见页文本上。
- 对每篇论文区分原始发现(paper_finding)、可迁移到2D LUT表达能力的思路(transfer_idea)与迁移限制(limitations)。
sources:
- source_id: conv2warp_1908.06194
  url: http://arxiv.org/abs/1908.06194
  title: 'Conv2Warp: An Unsupervised Deformable Image Registration with Continuous
    Convolution and Warping'
  decision: use
  selection_reason: 任务指定论文之一；其方法页（第2-4页）直接描述Catmull-Rom双三次样条重采样器的4基函数、C1连续、可微、局部支撑及相对线性插值的误差特性，是2D
    LUT高阶插值表达能力的核心参考。
  gap_ids:
  - gap_retrieve_two_papers
- source_id: instant_ngp_2201.05989
  url: http://arxiv.org/abs/2201.05989
  title: Instant neural graphics primitives with a multiresolution hash encoding
  decision: use
  selection_reason: 任务指定论文之二；其方法页（第2-3页）描述多分辨率网格/哈希编码：多分辨率级联网格、插值输出拼接、以及用哈希表大小T在参数数与重建质量间权衡，是2D
    LUT多分辨率/参数预算设计的参考。
  gap_ids:
  - gap_retrieve_two_papers
insights:
- id: insight_conv2warp_catmullrom
  source_id: conv2warp_1908.06194
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/e69592f9960246a980a672f288229c53.read.json
  document_sha256: 86f5bab54dbdb95f976403d1e83521a14b2fbe3c08bb03c960e9ea791fa3d877
  page: 3
  quote: Catmull-Rom spline consist of 4 basis functions with local support and are
    C1 continuous and differentiable (see Suppl. Mat. Section 1). These properties
    makes them smoother compared to standard linear interpolation techniques.
  paper_finding: Conv2Warp用可学习的双三次Catmull-Rom样条重采样器替代线性重采样来上采样变形向量场(DVF)；Catmull-Rom样条由4个基函数构成、具有局部支撑、C1连续且可微，因而比标准线性插值更平滑。作者在第4页图2实验显示Catmull-Rom插值在肺与脑数据集上训练损失最低，且非线性Conv2Warp模型比纯线性ConvNet对每种插值技术收敛更好。
  transfer_idea: 对二维实值LUT，可将固定均匀16×16网格上的双线性插值替换为Catmull-Rom双三次样条插值：4个基函数、C1连续、可微、局部支撑，在相同256个可训练格点参数下获得比线性更平滑且误差更小的连续映射，且因可微可端到端反向传播训练。
  limitations:
  - Catmull-Rom样条是插值型样条（过数据点），而论文指出B-spline不过数据点会引入更大插值误差；但Catmull-Rom对噪声/局部高曲率数据的过冲(overshoot)行为未在文中量化。
  - 论文面向图像配准的DVF上采样，网格为图像像素级稠密采样，未验证在稀疏16×16粗网格上表达局部高曲率标量场的能力。
  - C1连续仅保证一阶导数连续，对需要更高阶光滑性或二阶导数约束的LUT表达并不充分。
- id: insight_instantngp_multires
  source_id: instant_ngp_2201.05989
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/5ec62b853dcf4d7ea221ba1756d04189.read.json
  document_sha256: 8e1950c40dbee009b37349c77a8858fbfb6a8e9dbf135b5ce2c986b7dac76790
  page: 3
  quote: we use multiple separate hash tables indexed at different resolutions, whose
    interpolated outputs are concatenated before being passed through the MLP. The
    reconstruction quality is comparable to the dense grid encoding, despite having
    20× fewer parameters.
  paper_finding: Instant NGP用多分辨率哈希编码：多个不同分辨率的哈希表各自索引并插值特征向量，插值输出拼接后送入MLP；相比稠密网格编码，在参数少20倍时重建质量相当。第2页说明粗分辨率下网格点与数组项1:1映射，细分辨率下用空间哈希使多网格点别名同一数组项，碰撞梯度平均使大梯度主导，从而自动优先稀疏但重要的细尺度细节。
  transfer_idea: 对二维实值LUT，可在固定256参数预算内采用多分辨率结构：把预算分配到多个不同分辨率网格（而非单一16×16），各分辨率插值输出拼接，粗分辨率捕捉全局趋势、细分辨率捕捉局部高曲率细节，从而在相同参数数下提升表达能力。
  limitations:
  - Instant NGP依赖哈希碰撞的梯度平均来自动分配容量，需要MLP解码器来消歧碰撞；纯LUT（无MLP）无法利用该消歧机制，直接哈希会引入不可控别名误差。
  - 文中多分辨率示例为3D网格(16^3到173^3)与2D特征向量，参数规模(百万级)远超256参数预算，需重新设计分辨率级数与每级特征维度以适配256参数。
  - 多分辨率拼接要求各分辨率网格对齐到同一输入域，且插值输出维度随级数线性增长，在极低参数预算下每级可用特征维度会非常小。
---

已核实并读取两篇论文的方法页：Conv2Warp的Catmull-Rom双三次样条重采样器（4基函数、C1连续、局部支撑）与Instant NGP的多分辨率哈希编码（多分辨率网格、插值输出、参数权衡），作为二维实值LUT表达能力提案的检索来源。