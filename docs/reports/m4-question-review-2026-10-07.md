# M4 题目审阅稿

日期：2026-10-07。共六题；下面展示答案是为了审阅，普通练习接口不会在首次作答前返回答案或解析。

HTTP 三题来自真实 DeepSeek 输出；中点三题基于真实输出进行本地规范修订，没有新增供应商推理。代码代理做了逐题复核，独立人审尚未执行。

本次确定性检查仅验证引用、选项和集合判分，不能得出教学效果或知识准确率。

已处理的问题：旧中点讲解混写 C/Java，旧多选题遗漏非负下标条件；正文旧版已标记，旧多选题已停用，新版明确前提并保留历史。

## 复核依据

- [RFC 9112 消息体边界](https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3)
- [RFC 9110 字段行与 Set-Cookie 例外](https://www.rfc-editor.org/rfc/rfc9110.html#section-5.2)
- [Python 内建整数](https://docs.python.org/3/library/stdtypes.html#numeric-types-int-float-complex)
- [Java 整数加法](https://docs.oracle.com/en/java/javase/26/docs/specs/jls/jls-15.html#jls-15.18.2)
- [C 委员会表达式越界规则说明](https://open-std.org/jtc1/sc22/wg14/www/docs/n2396.htm)

## HTTP/1.1

### 1. 在 HTTP/1.1 中，一个请求报文既没有 Content-Length 也没有 Transfer-Encoding，服务端应如何判定该请求的消息体？

题型：single；考点：请求消息体边界判定：无长度且无传输编码时请求体长度为零

- `opt_zero_body`：消息体长度为零，不应再读取字节
- `opt_read_until_close`：一直读取直到连接关闭为止
- `opt_wait_chunked`：按分块传输方式解析后续字节
- `opt_reject_request`：视为报文非法，直接拒绝该请求

审阅用参考答案：opt_zero_body

正文第二节指出：请求若既无 Content-Length 也无 Transfer-Encoding，则消息体长度为零，服务端不应再读取字节。「由关闭连接定界」属于响应在无长度、无传输编码时的处理方式，不能套用到请求上。

依赖内容：content_d3d8887615f14e6eb39b115af2c240e7

### 2. 关于 HTTP/1.1 报文的消息体边界判定，下列哪些说法符合正文内容？

题型：multiple；考点：请求与响应边界规则不同，以及无体响应的判定条件

- `opt_resp_no_body_status`：1xx、204、304 以及 HEAD 的响应无消息体
- `opt_resp_order`：响应允许有体时，依次考虑 Transfer-Encoding、Content-Length，都没有则由关闭连接定界
- `opt_req_no_length_zero`：请求既无 Content-Length 也无 Transfer-Encoding 时，消息体长度为零
- `opt_connect_tunnel`：CONNECT 的 2xx 响应后连接转为隧道，不按普通消息体处理
- `opt_same_rule`：请求与响应在无长度时的判定规则完全相同

审阅用参考答案：opt_resp_no_body_status、opt_resp_order、opt_req_no_length_zero、opt_connect_tunnel

正文第二节明确：响应先判断是否允许有体（1xx、204、304、HEAD 无体；CONNECT 的 2xx 转隧道），允许有体时才依次看 Transfer-Encoding、Content-Length、关闭连接定界；而请求无长度即无体。因此「请求与响应规则完全相同」与正文相反，是常见误判。

依赖内容：content_d3d8887615f14e6eb39b115af2c240e7

### 3. 关于 HTTP/1.1 头部字段的合并，下列哪项符合正文的说明？

题型：single；考点：同名字段合并的列表语法要求与 Set-Cookie 例外

- `opt_setcookie_no_merge`：Set-Cookie 不适用逗号合并，多个 Set-Cookie 必须各占一行
- `opt_all_merge`：所有同名字段都可以用逗号合并成一行，包括 Set-Cookie
- `opt_merge_breaks_list`：同名字段合并后语义一定改变，因此任何字段都不应合并
- `opt_setcookie_semicolon`：多个 Set-Cookie 可以用分号合并成一行

审阅用参考答案：opt_setcookie_no_merge

正文第三节说明：同名字段可合并为一行，但必须符合列表语法；Set-Cookie 是例外，因为 Cookie 值本身可能含逗号（如 Expires 中的日期），合并会破坏语义，多个 Set-Cookie 必须各占一行。

依赖内容：content_d3d8887615f14e6eb39b115af2c240e7

## 一次中点比较与语言整数规则

### 1. 在数组 [1, 3, 5] 中查找目标值 3，当前区间为 l = 0、r = 2，取一次中点并比较。这一步的比较结果是什么？

题型：single；考点：能否说出本情境下一次中点比较的结果是相等

- `opt_equal`：相等，这一步就判定找到了目标值 3
- `opt_less`：中点值小于目标值，需要往右半区间继续找
- `opt_greater`：中点值大于目标值，需要往左半区间继续找
- `opt_none`：无法比较，因为中点没有取到整数下标

审阅用参考答案：opt_equal

区间 [0, 2] 下中点 m = 0 + (2 - 0) // 2 = 1，a[1] = 3，与目标值 3 相等，因此这一步就确认找到目标，不需要再看下标 0 或 2 的元素。小于或大于的分支属于完整二分查找流程，本节点不展开。

依赖内容：content_5afea4f90fdd41939b407f9d19e969e4

### 2. 在非负数组下标 0≤l≤r≤INT_MAX 的条件下，关于中点公式和语言整数规则，哪些说法正确？

题型：multiple；考点：能否辨析中点公式在定长整数下的溢出条件以及 Python 中的表现

- `opt_overflow_cond`：以 Java 32 位有符号 int 计算时，若数学和 l+r 超过上限，先求和会回绕，使中点不可靠
- `opt_diff_safe`：在题干条件下，r-l 与 l+floor((r-l)/2) 均在整数类型可表示范围内
- `opt_python_same`：Python 内建 int 任意精度，两种整数中点表达式的结果相同
- `opt_python_overflow`：在 Python 中 (l + r) // 2 也会溢出，必须改用 l + (r - l) // 2 才正确

审阅用参考答案：opt_overflow_cond、opt_diff_safe、opt_python_same

题干明确 0≤l≤r≤INT_MAX，因此 0≤r-l≤r，最终中点也在 [l,r] 内。Java int 加法溢出按低 32 位回绕；标准 C 的有符号溢出属于未定义行为，不能混写。Python 内建 int 是任意精度整数，两种写法结果相同。选项依赖题干前提，不泛化到任意负下标。

依赖内容：content_5afea4f90fdd41939b407f9d19e969e4

### 3. 在本节点情境（数组 [1, 3, 5]、目标值 3、一次取中点并比较）下，下列哪项描述是正确的？

题型：single；考点：能否辨析本步的边界取值与节点范围，不把完整二分查找流程混入

- `opt_m_is_1`：下标从 0 开始，区间为 [0, 2]，中点下标 m = 1，取出的值 a[1] = 3
- `opt_m_is_2`：下标从 0 开始，区间为 [0, 2]，中点下标 m = 2，取出的值 a[2] = 5
- `opt_m_is_0`：下标从 0 开始，区间为 [0, 2]，中点下标 m = 0，取出的值 a[0] = 1
- `opt_full_flow`：这一步之后必须按小于或大于的分支更新边界，否则无法得到比较结果

审阅用参考答案：opt_m_is_1

本节点情境下左边界 l = 0、右边界 r = 2（下标从 0 开始），中点 m = 0 + (2 - 0) // 2 = 1，a[1] = 3。比较结果本身不依赖边界更新，小于或大于的分支属于完整二分查找流程，本节点不展开。

依赖内容：content_5afea4f90fdd41939b407f9d19e969e4

## 尚待独立人审

请逐题检查：前提是否明确、答案是否唯一或集合完整、是否超出提供的正文、解析是否真正支持答案。独立人审和学习效果仍未知，不把代码代理检查替代为真人评分。
