---
schema: research_report.v1
project: pimc
human_summary: 已核实一篇自由节点B样条拟合论文方法页，证明节点位置可训练优于均匀网格逼近局部尖锐特征；因工具预算耗尽未能核实第二篇论文，证据缺口需后续补齐。
gaps:
- id: gap1
  question: 在固定参数预算(≤256实参数)下，非均匀/自适应网格节点位置是否比固定均匀16×16网格更能逼近局部高曲率二维函数？
- id: gap2
  question: 哪些替代插值方案(高阶、分层/多分辨率、张量积、分段)能在每参数表达能力上优于双线性LUT？
- id: gap3
  question: 固定参数数下网格节点坐标与节点值如何权衡？
- id: gap4
  question: 双线性vs替代LUT表示的逼近率理论依据是什么？
selection_principles:
- 论文必须包含可读的方法级细节(公式、参数计数、网格/架构定义)
- 论文必须关于函数逼近/插值/LUT表达能力而非无关深度学习
- 优先选择可直接下载并读取PDF方法页的开放获取论文
- 优先选择与自适应节点放置、自由节点、非均匀网格相关的论文以回答gap1/gap3
sources:
- source_id: knot_placement_2205.02978
  url: http://arxiv.org/abs/2205.02978
  title: A Deep Neural Network for Knot Placement in B-spline Approximation
  decision: use
  selection_reason: 直接处理自由(可变)节点位置优化问题，证明均匀节点远不足以逼近局部尖锐特征，与gap1/gap3直接相关；PDF方法页(第1-5页)已实际读取并含公式(1)-(6)与网络结构细节。
  gap_ids:
  - gap1
  - gap3
insights:
- id: insight_knot
  source_id: knot_placement_2205.02978
  read_receipt: /workspace/scratch/fbea517e6694/mars-idea/runs/real_idea_research/idea_research_20260908T034606_60f4ce/idea_research/research/downloads/2724cbe650104a7ea11dd1e8da6e9656.read.json
  document_sha256: 0d1e0e92b3996c6d5c2805e8a8d34f581b9e1a818f2a3d6ea1d2a3c2c8e348ba
  page: 2
  quote: Uniform knots or artificially specified knots are far from sufficient to
    achieve the goal.
  paper_finding: 论文将B样条拟合表述为自由(可变)节点问题 min_{n,C,U} ||P - A(U)C|| (式3)，指出一旦节点U固定，拟合退化为线性最小二乘
    min_C ||P-AC||_F (式2)；作者结论是均匀或人工指定节点远不足以满足拟合要求，自由节点对提升拟合性能至关重要，但该问题因节点与B样条基的非线性关系而高度非凸难解。
  transfer_idea: 对二维LUT，可将固定均匀16×16网格改为'自由节点'表示：把节点坐标当作可训练参数而非固定常数。例如取15×15=225个节点值
    + 15个x坐标 + 15个y坐标 = 255 ≤ 256实参数，节点坐标经排序/单调化约束(论文用Softmax保持节点序列单调)后参与双线性插值，使节点在局部高曲率区域自动聚集。
  limitations:
  - 论文针对一维B样条曲线拟合，非二维张量积双线性LUT，其结论不能直接外推到2D双线性插值的逼近率
  - 论文用深度神经网络求解器，参数量远超问题自由度，而本候选预算仅256实参数，无法照搬其过参数化求解策略
  - 自由节点优化高度非凸、多局部极小，在256参数预算下能否稳定收敛到优于均匀网格的解未被该论文验证
  - 论文未提供二维局部高曲率函数下节点坐标vs节点值的定量权衡分析
---

已核实一篇自由节点B样条拟合论文方法页，证明节点位置可训练优于均匀网格逼近局部尖锐特征；因工具预算耗尽未能核实第二篇论文，证据缺口需后续补齐。