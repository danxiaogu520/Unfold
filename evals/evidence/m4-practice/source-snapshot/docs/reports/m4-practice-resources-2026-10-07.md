# M4 练习、内容渲染与来源图像

日期：2026-10-07。M4 的工程实现和技术验证完成，继续 M5；独立人审未执行，六题审阅稿已提供。没有把结构合法、参考答案判分或文件可用声明为知识准确率和学习效果。

## 练习与版本

单次 1～5 题、默认三题。程序保存不可变题目、考点、选项 ID、答案、解析、实际正文 ID、设定版本与出题任务。三题及以上默认包含单/多选；两题也要求两种，单题不限其中哪种。`exercises-input.v2` 保存真实指令和必需题型，原 v1 用于历史读取。

单选按 ID，多选按完整集合判分，没有部分分或掌握度。重复请求保留同一个答案 ID，16 个竞争提交只保存一次；选项重排不改答案。新题产生新版本，不改旧作答。报告问题使具体题目停用，历史答案仍可重放；读旧正文只读取相应题目版本。

普通空间/节点响应、SSE 和公开 trace 不返回首次作答前的答案/解析；公开 trace 隐去出题候选和模型请求/响应。审阅文件为开发者明确列出参考答案，不能作为面向学习者的接口。当前出题针对单节点的实际正文，跨节点综合选择没有开放入口。

## 渲染与文件

Markdown/GFM、LaTeX 和代码高亮可读；HTML 跳过，Markdown 图片保持来源检查占位，代码标为未执行。图示使用 Mermaid 12.1.0 严格模式、全局纯 SVG 标签、固定主题，拒绝配置指令、外部 URL、点击和自定义样式，再经 DOMPurify 清理并置于无脚本 sandbox iframe。错误图示保持文字回退，正文继续显示。

截图复核发现最初只检查 SVG 容器不足以证明内容已绘制：新版 Mermaid 的全局 HTML 标签覆盖旧配置，清理后标签丢失。修复依据 [Mermaid 全局 htmlLabels 配置](https://mermaid.js.org/config/schema-docs/config.html#htmllabels)，检查增强为三个标签可见、独立截图与实际像素复核；修复后 2,693 个暗色像素，图示可见。原诊断失败保留，没有把初次容器检查当作最终验收。

Commons 通道查询实际 imageinfo/extmetadata，只接受许可清单、来源页及限定的 Wikimedia 图片主机。PNG/JPEG/WebP 下载最多 4 MB，解码最多 16 MP，拒绝 HTML、SVG、动画及不完整文件；重定向不跟随，路径不接受任意地址。文件按内容 SHA 保存、先 fsync 和原子改名，再登记候选；正式资源只引用存在且校验一致的文件。源数据处理依据 [MediaWiki imageinfo API](https://www.mediawiki.org/wiki/API:Imageinfo)。

实际获取的 [Binary Search Example](https://commons.wikimedia.org/wiki/File:Binary_Search_Example.png) 作者 Kurt Kaiser，声明 CC0；960×540、106,799 字节，摘要 `582637c6c34fbe7d68378c869beeee8fc89c38db906324f8aada44dbab91dbe0`。来源/作者/许可在界面和元数据中保存。图片是普通区间缩小的辅助示意，不称作溢出性质的证明。教学适配仍标为未核对。

正文与 outbox 原子提交，制作独立领取。队列满保持 pending；制作失败不回滚正文。当前内容/设定变化使旧资源任务冲突；复制旧正文保留原资源版本，过时请求不能修改新内容。文件丢失显示 missing、不给就绪地址；孤立文件可识别，不自动删除来源不明的用户文件。

## 真实实验及复核

预先选择已冻结的 `midpoint-1` 窄场景及已修订 HTTP 节点，最多三项模型任务、六次调用。实际 **4 次 DeepSeek 调用、3 项任务全部结构通过**：讲解一次格式失败后一次修复成功，两个三题集合各一次成功；一项真实 Commons 制作成功。新增保守费用 **0.073738 元**，用量未知 0。累计 M0～M4 **1.278668 元**，10 元总授权剩余 **8.721332 元**。金额为保守估算，逐次供应商账单归因未知。

代码代理发现讲解混写标准 C 与 Java 溢出行为，多选题遗漏非负下标条件。参照 [Python 整数](https://docs.python.org/3/library/stdtypes.html#numeric-types-int-float-complex)、[Java 加法规则](https://docs.oracle.com/en/java/javase/26/docs/specs/jls/jls-15.html#jls-15.18.2) 和 [C 委员会说明](https://open-std.org/jtc1/sc22/wg14/www/docs/n2396.htm)，本地修订明确 `0≤l≤r≤INT_MAX`、C 有符号溢出未定义、Java int 回绕及 Python 内建任意精度整数。

旧正文 v3 为 reported、旧多选题 disabled，新正文 v4 和题集 v2 另存，仍为 unverified。修订通过当前契约、依赖与提交资格检查，记录 `agent_reference_correction`，**不算新供应商推理**；图片复用已验证旧文件，不重新下载。模型修订请求曾被自动审批以可能外发私有内容为由拒绝，最终使用本地修订替代，没有绕过拒绝或增加费用。

[六题审阅稿](m4-question-review-2026-10-07.md) 保存原 HTTP 题和修订后的中点题、来源及边界。12 个正确/错误集合样例按已保存参考答案验证；这是判分检查，不是学习者得分或独立人审。HTTP 题按此前已读取的两份 RFC 做定向对照，没有声称全面网络知识核验。

## 故障与技术验收

**180 项 Python 通过、0 跳过，Chromium 7/7、统一入口 11 项检查通过。** 36 份 schema、严格类型、格式、构建、依赖锁和 M0～M3 历史校验通过；声明 API 更新另外通过契约检查。npm audit 最终 0 条漏洞。公式依赖曾有低风险传递版本告警，统一到已有固定 KaTeX 0.19.0 后审计和公式浏览器检查通过；没有使用强制降级。

资源 SIGKILL 四窗口均通过：文件已写/候选前可识别孤立文件；候选保存后恢复不再获取；正式提交前共同回滚；提交后读取原版本不再执行。文件与数据库并非跨系统原子事务，先后顺序和孤立文件识别是明确设计，没有把它表述成任意故障保证。

浏览器验证实际 PostgreSQL、HTTP、题目隐藏/单多选/停用/历史、图示可见文字、公式/高亮、恶意 HTML/配置回退、来源图片和刷新不生成。CI 图像使用已取得并带许可的记录文件，明确标 `recorded_fixture_replay`，不算又一次真实获取。真实获取另有完整 tool trace 和元数据。

Mermaid 布局的若干延迟加载块超过 500 KB，构建提示保留；初始入口约 239 KB。测试不覆盖任意浏览器、图示或极端文本。远程 CI 未执行。网页隔离、定制网页、资源 Agent、上下文对照和部署演示继续 M5～M7。

```sh
export UNFOLD_TEST_DATABASE_URL=postgresql+psycopg://unfold@127.0.0.1:54318/unfold
.venv/bin/python scripts/check.py --browser
.venv/bin/python scripts/verify_practice_evidence.py
```

证据在 [`evals/evidence/m4-practice`](../../evals/evidence/m4-practice)。真实运行目录不可覆盖；离线校验器不再次推理、下载或执行归档代码。
