---
schema: research_report.v1
project: pimc
human_summary: 已实际读取并核验ICELUT论文方法页(1-4页)，确认LUT尺寸随输入维度指数增长、MSB/LSB拆分与split FC分组压缩等参数效率结论；第二篇候选Instant-NGP因PDF超过12MiB下载限制失败，且本循环工具预算已耗尽，无法取得第二篇独立论文的方法页证据，故证据不足，仅交付1篇已核验论文。
gaps:
- id: g1
  question: 在固定参数预算下，自适应/非均匀网格节点布置是否比固定均匀网格更能逼近局部高曲率二维函数？
- id: g2
  question: 替代插值方案（高阶、分层/多分辨率）是否提升每参数表达能力？
selection_principles:
- 优先选择直接讨论可训练查找表(LUT)与插值、且受参数预算约束的论文
- 优先选择方法页含具体公式、可迁移到二维标量LUT(≤256实参数)的论文
- 只采用实际下载并读取PDF方法页的论文作为use证据；未读取方法页的候选一律defer
sources:
- source_id: icelut
  url: http://arxiv.org/abs/2403.19238
  title: Taming Lookup Tables for Efficient Image Retouching
  decision: use
  selection_reason: 纯LUT图像增强论文，方法页直接讨论LUT尺寸随输入维度指数增长、MSB/LSB拆分降低地址数、split FC层降低存储，与固定参数预算下LUT表达能力问题高度相关；PDF方法页(1-4页)已实际下载读取并核验引文。
  gap_ids:
  - g1
  - g2
- source_id: instantngp
  url: http://arxiv.org/abs/2201.05989
  title: Instant neural graphics primitives with a multiresolution hash encoding
  decision: defer
  selection_reason: 多分辨率哈希编码+三线性插值的可训练网格，直接对应分层插值提升每参数表达能力的假设(g2)；但PDF下载因超过12MiB限制失败，方法页未实际读取，故defer而非use。
  gap_ids:
  - g2
insights:
- id: i1
  source_id: icelut
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T034606_60f4ce/idea_research/research/downloads/98de766267b243c3972fdf7797fec41d.read.json
  document_sha256: 54f0cc7e37572a240361cfb3fe7bb538346d1605c5b35f99adaa6a7bb7d7a05c
  page: 2
  quote: Since the LUT size scales exponentially with the input dimension [9,12,15],
    we first studied the impact of receptive field size (spatial) and the number of
    input channels (depth) on performance.
  paper_finding: 论文结论：LUT尺寸随输入维度指数增长，因此作者将8-bit输入拆成4个最高有效位(MSB)与4个最低有效位(LSB)两个并行分支，把LUT地址从256^3降到2×16^3；并用split
    FC层把存储从(V)^C降到L×(V)^K（V为可能输入值数、C为通道数、L为组长度、K为组数，C=L×K）。
  transfer_idea: 迁移到二维标量LUT：把256个实参数预算视为地址空间约束，可借鉴'按位拆分/分组'思想——例如把16×16均匀网格的坐标量化拆成粗/细两级，用少量参数编码粗网格、细网格只在高曲率区域激活，从而在≤256参数内提高局部分辨率而不指数膨胀。
  limitations:
  - 论文面向图像颜色增强(3D RGB→RGB映射)，非二维标量函数逼近，其'通道数/感受野'结论不能直接照搬到标量回归
  - MSB/LSB拆分针对8-bit整数颜色输入，二维连续坐标(x,y)∈[-1,1]^2需先量化，量化误差会引入额外偏差
  - 论文未讨论自适应/非均匀节点布置，其网格仍是规则LUT，不能直接回答g1的非均匀节点假设
- id: i2
  source_id: icelut
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T034606_60f4ce/idea_research/research/downloads/98de766267b243c3972fdf7797fec41d.read.json
  document_sha256: 54f0cc7e37572a240361cfb3fe7bb538346d1605c5b35f99adaa6a7bb7d7a05c
  page: 3
  quote: It reduces the memory from(V )C to L× (V )K, whereV, C, L and K stand for
    the possible input values, channel numbers, group length and number of groups,
    respectively, withC =L×K.
  paper_finding: 论文方法页给出split FC层的存储压缩公式：把输入特征分成K组、每组长度L，用独立FC层处理每组，存储从(V)^C降到L×(V)^K，所有输出求和得到权重向量用于线性组合基LUT。
  transfer_idea: 迁移到二维LUT：把256个可训练实参数按'分组低秩'结构组织——例如把16×16=256个节点值分解为若干低秩分量(如K个秩1项)，用远少于256个独立参数表达，把省下的预算用于更高阶插值或局部细化，从而提升每参数表达能力。
  limitations:
  - 该公式针对FC层权重存储，非LUT节点值本身，迁移需重新推导二维节点矩阵的低秩分解形式
  - 低秩假设对局部高曲率函数可能失效——高曲率区域需要高秩/局部自由度，低秩压缩反而损失局部精度
  - 论文未做标量函数逼近的误差分析，无法直接给出低秩分解对逼近误差的定量保证
---

已实际读取并核验ICELUT论文方法页(1-4页)，确认LUT尺寸随输入维度指数增长、MSB/LSB拆分与split FC分组压缩等参数效率结论；第二篇候选Instant-NGP因PDF超过12MiB下载限制失败，且本循环工具预算已耗尽，无法取得第二篇独立论文的方法页证据，故证据不足，仅交付1篇已核验论文。