import Image from "next/image";

type Project = {
  title: string;
  period: string;
  role: string;
  direction: string;
  summary: string;
  proof: string[];
  links: Array<{ label: string; href: string }>;
  image: string;
};

type Writing = {
  title: string;
  summary: string;
  status: string;
};

const projects: Project[] = [
  {
    title: "MARS · Multi-Agent Research System",
    period: "2025.12 - 至今",
    role: "Owner / 开发者",
    direction: "Harness, Multi-Agent, HITL, Post-Train",
    summary:
      "面向研究链路的多 Agent 底座，用可验证输出、人工门禁和完整 run 沉淀，把 idea、实验、编码、执行和写作串成端到端闭环。",
    proof: [
      "以 Bridge + Harness 解耦产品编排与可信机制",
      "支持 mock pipeline：无 API key、无 GPU 也能跑通 demo",
      "Coding Agent 预留 remote API / local vLLM 后训练模型加载路径",
    ],
    links: [
      { label: "GitHub", href: "https://github.com/HarryYangthu" },
      { label: "Demo soon", href: "#contact" },
    ],
    image: "/personal/mars.png",
  },
  {
    title: "端侧自适应 MoE · 动态非线性系统",
    period: "2024.08 - 2025.12",
    role: "研究负责人 / 算法 Owner",
    direction: "MoE, 复值神经网络, CNN, 时序预测",
    summary:
      "面向动态非线性系统的端侧模型设计，聚焦 router、model block 与时序预测稳定性，在算力、泛化与部署复杂度之间做工程化折中。",
    proof: [
      "负责核心建模路线与实验拆解",
      "将复杂值网络能力引入非线性时间序列建模",
      "围绕端侧约束设计可落地的模型结构",
    ],
    links: [
      { label: "Case notes", href: "#writing" },
      { label: "Demo soon", href: "#contact" },
    ],
    image: "/personal/intro.png",
  },
  {
    title: "运维领域大模型 · RAG + Agent",
    period: "2024.12 - 2025.06",
    role: "参与者 / 主责 RAG",
    direction: "LLM Agent, RAG, SFT, DPO",
    summary:
      "把维护文档、历史案例、生产数据和工具调用聚合到一个 AI 助手入口，帮助工程师从故障、日志和制程参数中更快定位根因。",
    proof: [
      "44,948 条坏件日志诊断分析处理量",
      "1,764 知识问答 PV，1,047 工具助手 PV",
      "SFT + DPO + RAG 诊断准确率从 68% 提升到 81%",
    ],
    links: [
      { label: "RAG design", href: "#writing" },
      { label: "Demo soon", href: "#contact" },
    ],
    image: "/personal/ops-llm.png",
  },
];

const writings: Writing[] = [
  {
    title: "从零设计一个 Multi-Agent Research System",
    summary: "解释 Bridge、Harness、Schema、HITL Gate 和 run 沉淀为什么必须一起设计，而不是最后拼装。",
    status: "Deep dive",
  },
  {
    title: "Agent 输出为什么必须 Schema 化",
    summary: "从 markdown + YAML frontmatter 到 JSON Schema 校验，讲清楚可读性和可执行性如何同时成立。",
    status: "Architecture",
  },
  {
    title: "RAG 在工业运维场景里的失败模式",
    summary: "拆解编码精确匹配、跨文档拼接、过时文档干扰三类 badcase，以及混合检索与重排策略。",
    status: "Case study",
  },
];

const metrics = [
  { label: "坏件日志诊断分析", value: "44,948", caption: "项目累计处理量" },
  { label: "知识问答 PV", value: "1,764", caption: "RAG 直接使用入口" },
  { label: "工具助手 PV", value: "1,047", caption: "Agent 工具协同入口" },
  { label: "诊断准确率", value: "81%", caption: "SFT + DPO + RAG" },
];

const timeline = [
  { time: "2026", title: "MARS V0", detail: "以 mock pipeline 优先跑通端到端研究链路，再逐步替换真实 KB、LLM 与 execution。" },
  { time: "2025", title: "智能体架构", detail: "从单点 LLM 能力转向 Harness、HITL、后训练加载和自进化 Agent 工程体系。" },
  { time: "2024", title: "运维大模型", detail: "主责 RAG 链路，完成文档清洗、双索引、query 预处理、混合召回与 badcase 闭环。" },
  { time: "2023", title: "AI 算法工程", detail: "在通信、非线性系统建模和工程诊断场景中积累算法落地经验。" },
];

const skills = [
  "Multi-Agent Systems",
  "Research Harness",
  "RAG / Hybrid Retrieval",
  "Post-Training Loader",
  "MoE / Time Series",
  "FastAPI / Next.js",
  "vLLM / Qwen",
  "HITL Workflow",
];

export default function PersonalSite(): JSX.Element {
  return (
    <main className="min-h-screen bg-[#f6f3ed] text-[#151713]">
      <section className="border-b border-[#d9d2c3] bg-[#fcfaf5]">
        <div className="mx-auto grid min-h-[92vh] max-w-7xl grid-cols-1 gap-10 px-5 py-6 md:grid-cols-[minmax(0,1.04fr)_minmax(360px,0.96fr)] md:px-8 lg:px-10">
          <div className="flex flex-col justify-between gap-12">
            <nav className="flex items-center justify-between text-sm text-[#5f6658]">
              <a className="font-semibold text-[#151713]" href="#">
                Harry Yang
              </a>
              <div className="flex gap-4">
                <a className="transition hover:text-[#0b7a67]" href="#projects">
                  Projects
                </a>
                <a className="transition hover:text-[#0b7a67]" href="#writing">
                  Blog
                </a>
                <a className="transition hover:text-[#0b7a67]" href="#contact">
                  Contact
                </a>
              </div>
            </nav>

            <div className="max-w-3xl">
              <p className="mb-5 text-sm font-semibold uppercase tracking-[0.22em] text-[#0b7a67]">
                AI Algorithm · Agent Architecture · Research Lead
              </p>
              <h1 className="text-5xl font-semibold leading-[1.02] text-[#11130f] md:text-7xl">
                把研究问题变成可运行、可验证、可沉淀的 AI 系统。
              </h1>
              <p className="mt-7 max-w-2xl text-lg leading-8 text-[#4d5348]">
                我是杨宏俊 Harry，清华人工智能硕士、牛津 AI&ML 访问学者，现聚焦研究型 Multi-Agent
                系统、工业 RAG、通信算法与端侧智能模型。从算法原型到工程闭环，我更关心系统能不能在真实约束里持续迭代。
              </p>
              <div className="mt-8 flex flex-wrap gap-3">
                <a
                  className="rounded-md bg-[#11130f] px-5 py-3 text-sm font-semibold text-white transition hover:bg-[#0b7a67]"
                  href="#projects"
                >
                  查看项目证据
                </a>
                <a
                  className="rounded-md border border-[#b9b09f] px-5 py-3 text-sm font-semibold text-[#151713] transition hover:border-[#0b7a67] hover:text-[#0b7a67]"
                  href="https://github.com/HarryYangthu"
                >
                  GitHub
                </a>
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3 text-sm text-[#4d5348] md:grid-cols-4">
              <div>
                <div className="text-2xl font-semibold text-[#151713]">16</div>
                <div>高级工程师</div>
              </div>
              <div>
                <div className="text-2xl font-semibold text-[#151713]">2.7y</div>
                <div>AI 工程经验</div>
              </div>
              <div>
                <div className="text-2xl font-semibold text-[#151713]">14</div>
                <div>团队协作规模</div>
              </div>
              <div>
                <div className="text-2xl font-semibold text-[#151713]">3+</div>
                <div>核心项目沉淀</div>
              </div>
            </div>
          </div>

          <div className="flex items-end pb-6">
            <div className="w-full overflow-hidden rounded-md border border-[#cbc2b0] bg-white shadow-[0_24px_70px_rgba(43,38,26,0.16)]">
              <Image
                alt="Harry Yang interview introduction deck preview"
                className="h-auto w-full"
                height={810}
                priority
                src="/personal/intro.png"
                width={1440}
              />
              <div className="grid grid-cols-3 border-t border-[#e4ddcf] bg-[#f8f5ee] text-sm">
                <div className="border-r border-[#e4ddcf] p-4">
                  <div className="font-semibold text-[#151713]">MARS</div>
                  <div className="mt-1 text-[#63695d]">研究型 Agent 底座</div>
                </div>
                <div className="border-r border-[#e4ddcf] p-4">
                  <div className="font-semibold text-[#151713]">RAG</div>
                  <div className="mt-1 text-[#63695d]">工业场景闭环</div>
                </div>
                <div className="p-4">
                  <div className="font-semibold text-[#151713]">MoE</div>
                  <div className="mt-1 text-[#63695d]">端侧智能模型</div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="border-b border-[#d9d2c3] bg-[#f6f3ed] px-5 py-12 md:px-8 lg:px-10">
        <div className="mx-auto grid max-w-7xl gap-5 md:grid-cols-4">
          {metrics.map((metric) => (
            <div key={metric.label} className="rounded-md border border-[#d8cfbd] bg-[#fffdf8] p-5">
              <div className="text-4xl font-semibold text-[#0b7a67]">{metric.value}</div>
              <div className="mt-3 font-semibold">{metric.label}</div>
              <div className="mt-1 text-sm text-[#656b60]">{metric.caption}</div>
            </div>
          ))}
        </div>
      </section>

      <section id="projects" className="bg-[#fcfaf5] px-5 py-16 md:px-8 lg:px-10">
        <div className="mx-auto max-w-7xl">
          <div className="mb-9 flex flex-col justify-between gap-5 md:flex-row md:items-end">
            <div>
              <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#b4512a]">Selected Projects</p>
              <h2 className="mt-3 text-4xl font-semibold">项目不是清单，是证据链。</h2>
            </div>
            <p className="max-w-xl text-base leading-7 text-[#565d51]">
              每个项目保留在线 demo 或案例入口位，后续可以替换为真实部署地址、GitHub star、真实用户数和技术报告链接。
            </p>
          </div>

          <div className="grid gap-5">
            {projects.map((project, index) => (
              <article
                key={project.title}
                className="grid overflow-hidden rounded-md border border-[#d8cfbd] bg-[#fffdf8] md:grid-cols-[0.88fr_1.12fr]"
              >
                <div className={`${index % 2 === 1 ? "md:order-2" : ""} bg-[#ece5d7]`}>
                  <Image
                    alt={`${project.title} deck preview`}
                    className="h-full min-h-[260px] w-full object-cover"
                    height={810}
                    src={project.image}
                    width={1440}
                  />
                </div>
                <div className="p-6 md:p-8">
                  <div className="flex flex-wrap gap-2 text-xs font-semibold text-[#5d6258]">
                    <span>{project.period}</span>
                    <span>/</span>
                    <span>{project.role}</span>
                  </div>
                  <h3 className="mt-4 text-3xl font-semibold">{project.title}</h3>
                  <p className="mt-3 text-sm font-semibold text-[#0b7a67]">{project.direction}</p>
                  <p className="mt-5 text-base leading-7 text-[#50574c]">{project.summary}</p>
                  <ul className="mt-6 grid gap-3 text-sm text-[#33372f]">
                    {project.proof.map((item) => (
                      <li key={item} className="flex gap-3">
                        <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-[#b4512a]" />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                  <div className="mt-7 flex flex-wrap gap-3">
                    {project.links.map((link) => (
                      <a
                        key={`${project.title}-${link.label}`}
                        className="rounded-md border border-[#bdb4a4] px-4 py-2 text-sm font-semibold text-[#151713] transition hover:border-[#0b7a67] hover:text-[#0b7a67]"
                        href={link.href}
                      >
                        {link.label}
                      </a>
                    ))}
                  </div>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section id="writing" className="border-y border-[#d9d2c3] bg-[#181a17] px-5 py-16 text-[#f7f2e8] md:px-8 lg:px-10">
        <div className="mx-auto grid max-w-7xl gap-8 lg:grid-cols-[0.8fr_1.2fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#f3a25f]">Technical Blog</p>
            <h2 className="mt-3 text-4xl font-semibold">三篇深度文章，建立技术判断。</h2>
            <p className="mt-5 text-base leading-7 text-[#c8c0b0]">
              文章选题来自 PPT 中最有辨识度的部分：Agent 架构、Schema 化工作流、工业 RAG 失败模式。它们比泛泛的项目介绍更能体现技术深度。
            </p>
            <div className="mt-7 flex flex-wrap gap-2">
              {skills.map((skill) => (
                <span key={skill} className="rounded-md border border-[#4b5148] px-3 py-2 text-sm text-[#ded7c9]">
                  {skill}
                </span>
              ))}
            </div>
          </div>
          <div className="grid gap-4">
            {writings.map((writing) => (
              <article key={writing.title} className="rounded-md border border-[#3d433c] bg-[#20231f] p-6">
                <div className="text-sm font-semibold text-[#f3a25f]">{writing.status}</div>
                <h3 className="mt-3 text-2xl font-semibold">{writing.title}</h3>
                <p className="mt-3 leading-7 text-[#c8c0b0]">{writing.summary}</p>
              </article>
            ))}
          </div>
        </div>
      </section>

      <section className="bg-[#fcfaf5] px-5 py-16 md:px-8 lg:px-10">
        <div className="mx-auto grid max-w-7xl gap-8 lg:grid-cols-[0.7fr_1.3fr]">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#0b7a67]">Milestones</p>
            <h2 className="mt-3 text-4xl font-semibold">时间线让能力演进可见。</h2>
          </div>
          <div className="grid gap-4">
            {timeline.map((item) => (
              <div key={`${item.time}-${item.title}`} className="grid gap-4 border-b border-[#ded6c7] pb-5 md:grid-cols-[110px_1fr]">
                <div className="text-2xl font-semibold text-[#b4512a]">{item.time}</div>
                <div>
                  <h3 className="text-xl font-semibold">{item.title}</h3>
                  <p className="mt-2 leading-7 text-[#565d51]">{item.detail}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      <section id="contact" className="bg-[#0f110e] px-5 py-14 text-[#f7f2e8] md:px-8 lg:px-10">
        <div className="mx-auto flex max-w-7xl flex-col justify-between gap-6 md:flex-row md:items-center">
          <div>
            <p className="text-sm font-semibold uppercase tracking-[0.2em] text-[#8bd3c7]">Available for</p>
            <h2 className="mt-3 text-3xl font-semibold">AI Agent 架构、工业 RAG、通信算法与研究工程化讨论。</h2>
          </div>
          <div className="flex flex-wrap gap-3">
            <a className="rounded-md bg-[#f7f2e8] px-5 py-3 text-sm font-semibold text-[#11130f]" href="mailto:361612560@qq.com">
              361612560@qq.com
            </a>
            <a className="rounded-md border border-[#4d544a] px-5 py-3 text-sm font-semibold text-[#f7f2e8]" href="https://github.com/HarryYangthu">
              github.com/HarryYangthu
            </a>
          </div>
        </div>
      </section>
    </main>
  );
}
