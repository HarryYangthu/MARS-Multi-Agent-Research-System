---
schema: research_report.v1
project: pimc
human_summary: 调研了两篇论文：Conv2Warp的Catmull-Rom双三次插值（16点模板、C1连续可微、网格参数不变仍为256）可直接替换双线性核；Instant
  NGP的多分辨率网格/哈希编码提供局部高曲率表达思路但依赖MLP与特征向量，参数模型不直接匹配。
gaps:
- id: g1
  question: 在固定均匀16×16网格、256个可训练实参数约束下，有哪些替代双线性插值的插值/无插值方案（双三次、Catmull-Rom、径向基、高阶样条、张量积基、局部多项式、无网格参数化）能提升局部高曲率或非均匀采样函数的表达能力，其参数数、边界处理、初始化、可微性与训练性质如何？
selection_principles:
- 优先选择给出具体插值公式、参数计数与训练/可微性细节、且面向可学习二维标量映射的论文
- 优先选择方法页可直接读取、能区分论文自身发现与可迁移思路的论文
- 优先选择与固定均匀网格+标量参数约束兼容的方案（核替换而非改变参数结构）
sources:
- source_id: conv2warp
  url: http://arxiv.org/abs/1908.06194
  title: 'Conv2Warp: An Unsupervised Deformable Image Registration with Continuous
    Convolution and Warping'
  decision: use
  selection_reason: 方法页明确给出可学习双三次Catmull-Rom样条重采样器，其4基函数、局部支撑、C1连续可微性质可直接迁移为2D LUT的插值核替换，且不改变网格参数数量。
  gap_ids:
  - g1
- source_id: instant_ngp
  url: http://arxiv.org/abs/2201.05989
  title: Instant neural graphics primitives with a multiresolution hash encoding
  decision: use
  selection_reason: 方法页给出多分辨率网格/哈希编码的插值稀疏更新性质与多分辨率分解，为局部高曲率表达提供概念性迁移思路，但其依赖MLP与多维特征向量，参数模型与纯256标量LUT不完全匹配。
  gap_ids:
  - g1
insights:
- id: i1
  source_id: conv2warp
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/0b0c861652d647df98631007c4839249.read.json
  document_sha256: 86f5bab54dbdb95f976403d1e83521a14b2fbe3c08bb03c960e9ea791fa3d877
  page: 3
  quote: Catmull-Rom spline consist of 4 basis functions with local support and are
    C1 continuous and differentiable (see Suppl. Mat. Section 1). These properties
    makes them smoother compared to standard linear interpolation techniques.
  paper_finding: 论文自身发现：在可变形图像配准中，用可学习双三次Catmull-Rom样条重采样器替代线性重采样，能得到更平滑、插值误差更小的形变场，且比线性卷积+线性重采样更准确。
  transfer_idea: 将2D LUT的插值核从双线性（4点模板）替换为双三次Catmull-Rom（4×4=16点模板，张量积4个1D三次基函数），网格仍为固定16×16、256个标量参数不变，仅改变核；Catmull-Rom是C1连续且可微的，故对网格参数可端到端反向传播，且因过数据点（插值型）比B样条误差更小。
  limitations:
  - 论文未在主文给出1D三次核的显式系数公式（指向补充材料），迁移需自行补全标准Catmull-Rom核系数
  - Catmull-Rom在边界处需额外处理（如重复/镜像端点或退化为低阶核），论文未讨论2D LUT边界策略
  - 论文面向图像配准的形变场（多通道、多层金字塔、L2正则），非纯标量回归，其正则与多尺度策略不能直接照搬
  - 16点模板使每个样本反向传播触及16个网格参数（vs 双线性4个），稀疏更新收益略降
- id: i2
  source_id: instant_ngp
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T040828_074328/idea_research/research/downloads/a6c13c83ce5e47dca0639b844605ae11.read.json
  document_sha256: 8e1950c40dbee009b37349c77a8858fbfb6a8e9dbf135b5ce2c986b7dac76790
  page: 3
  quote: we use multiple separate hash tables indexed at different resolutions, whose
    interpolated outputs are concatenated before being passed through the MLP.
  paper_finding: 论文自身发现：多分辨率哈希/网格编码比单分辨率稠密网格用更少参数达到相当重建质量（文中称'despite having 20× fewer
    parameters'），粗分辨率捕获平滑结构、细分辨率捕获局部细节，且插值网格的稀疏更新使训练高效。
  transfer_idea: 对2D LUT可采用多分辨率网格分解：把256个标量参数分配到若干共位网格（如粗8×8=64 + 细16×16=192，或粗/中/细多级），各级分别做双线性插值后求和/加权，从而在固定256参数内同时表达平滑背景与局部高曲率细节；插值对参数可微，可端到端训练。
  limitations:
  - 论文编码使用多维特征向量并经MLP输出，非纯标量LUT；其参数计数模型（哈希表大小T+MLP权重）与256标量约束不直接匹配
  - 哈希碰撞消歧依赖MLP学习，纯标量求和式多分辨率LUT无此机制，需自行设计碰撞/权重分配
  - 论文面向3D NeRF/图像等任务，未给出2D标量回归的初始化与边界处理规范
  - 多分辨率网格若各级独立插值后求和，需验证总参数≤256下的最优分辨率分配与初始化
---

调研了两篇论文：Conv2Warp的Catmull-Rom双三次插值（16点模板、C1连续可微、网格参数不变仍为256）可直接替换双线性核；Instant NGP的多分辨率网格/哈希编码提供局部高曲率表达思路但依赖MLP与特征向量，参数模型不直接匹配。