# Tasks

## 1. 批次结果与入口

- [x] 1.1 定义 `PhaseResult`、`BatchResult` 和退出码映射，显式包含 `barrier_passed`、`cleanup_complete`、`output_healthy`、counts、errors、reason，覆盖 completed/unchanged/failed/stopped、disabled 与多错误；用纯单元测试验证 0/1/2/3/130 及“单项错误可继续/边界错误阻断”矩阵。
- [x] 1.2 实现批次单实例锁：在登录、阶段初始化和批次日志前获取，冲突立即返回 code 3 并只向 stderr 提示，确保异常退出后锁可恢复；用两个进程竞争锁测试。
- [x] 1.3 将 `scheduler.py` 默认入口改为单进程一次性协调器，配置冲突检查先于登录；仅在至少一个阶段启用时懒加载登录客户端，规范化 `SystemExit` 并传播最终退出码；用 fake phases 断言图片边界、小说边界和最终退出顺序。
- [x] 1.4 实现图片/小说/全部关闭及 crawler/API 冲突配置矩阵；用初始化 spy 断言禁用阶段不导入/构造，冲突在任何阶段前返回 code 2。
- [x] 1.5 让阶段错误、初始化异常、`SystemExit` 和顶层未知异常进入统一结果，避免 `@logger.catch` 吞掉失败；阶段初始化异常通过 `PhaseResult.init_failed` 映射为 code 2。

## 2. 线程池与图片完成屏障

- [x] 2.1 重构 `ThreadPool` 保存并锁定实际 Thread，修复启动注册竞态、队列容量、close 幂等、停止提交和可靠 sentinel；close 保持旧调用兼容且不因满队列永久阻塞，put 在停止/关闭/期限时明确返回或抛出并计入取消，待执行任务由 join 阶段 drain/取消记账；用快速启动/关闭及满队列测试验证，并更新 crawler 的 close 后 join/terminate 兼容路径。
- [x] 2.2 增加任务、回调完成计数与异常收集，提供真正 `join()`，并保持 `pool.put(func, args, callback(status, result))` 兼容；worker 或 callback 的单项异常在全部回调完成且日志健康时计入 errors 但不自动阻断 barrier，池线程未退出或 join 失败才阻断；用延迟 worker、延迟 callback、任务异常、callback 异常测试验证返回前全部完成。
- [x] 2.3 让 Bookmark 的单轮适配在分页结束后停止提交、关闭并 join，传播 worker/callback/分页失败；用 fake downloader 和超过旧限制的分页测试验证完整列表与失败摘要。
- [x] 2.4 补充 stop_event/deadline 从协调器传递到 Bookmark/Crawler worker、Timer 和重试等待的可中断提交与等待，区分排队取消、活动任务收尾和 deadline failure；协调器现在按配置创建并传递阶段 deadline。
- [x] 2.5 修正公开/私密列表各自 offset 从零开始，移除 batch 路径的旧 day_count 截断和下一轮 sleep；用前列表失败、列表变更、无变化回归测试验证。

## 3. 更新、下载和一致性失败传播

- [x] 3.1 将 `Bookmark.check_update` 改为 changed/unchanged/error，验证所有启用列表后再决策；用网络、未登录、无权限、空列表和数据库异常 fake 响应测试。
- [x] 3.2 修复 `downer.py` 详情、filter、Single/Multi/Gif 链路的成功/失败传播和可中断退避；用必要文件缺失、预期过滤、404 和瞬态网络失败测试。
- [x] 3.3 让 insert/update False、必要下载失败和未完成元数据不计为 success；用数据库关闭、写入失败和部分下载场景验证阶段结果及幂等保留。
- [x] 3.4 为 `checker.py` 与严格 DB 查询增加统计和异常传播，包含 `check_illust`/最新记录查询的数据库异常，保留最近 20 条检查、区分 unavailable/skipped/error；用查询异常、修复失败、作品详情 404 不重试并保留文件/记录、内嵌图片 404 生成失败测试。
- [x] 3.5 将 `ptimer.py` 递归等待改为有上限且可停止的循环，修正请求前上限检查；让 Downloader、novel retry 和 login 请求使用可中断等待/明确 timeout，并用定时器与登录异常测试。Novel 更新检查、分页和一致性检查现在继承阶段 deadline。

## 4. 日志上下文与输出屏障

- [x] 4.1 在 `log_record.py` 增加批次/阶段上下文和线程安全 sink 错误状态；保持控制台实时输出、TTY skip 聚合和文件逐条记录，并已通过上下文注入与重定向测试。
- [x] 4.2 实现阶段结束 helper：按批次/阶段清零并检查所有控制台与异步文件 sink 的线程安全错误状态，清理聚合行并等待 `logger.complete()`；已通过 sink 故障和晚到错误测试。
- [x] 4.3 图片阶段在 worker、callback、汇总和健康日志均完成后输出 `IMAGE_PHASE_DONE`；已通过阶段顺序及单项错误继续测试。
- [x] 4.4 为共享 Downloader、DB、checker 以及 worker 传递正确阶段上下文，确保图片阶段无小说记录、小说初始化和所有进度均在标记后；已通过禁用阶段上下文测试。

## 5. 小说阶段与协作停止

- [x] 5.1 将 `Novel.run()` 适配为一次性结果契约，保留最新 10 条更新判断、无变化时仍执行最近 20 条一致性检查、EPUB 生成和数据库 upsert 异常传播；用成功、无变化、内嵌图缺失、upsert 失败和初始化失败测试。
- [x] 5.2 仅在图片完成标记及第二次日志检查成功后懒导入/构造 Novel，并确认图片阶段 Downloader/DB/worker 已关闭；用 spy 断言小说 DB 表初始化、检查和下载日志全部位于图片日志之后。
- [x] 5.3 增加 SIGINT/Ctrl+C 及 POSIX SIGTERM handler，仅设置 stop_event；登录、请求、分页、作品、重试和队列已接收停止上下文，并已通过注入信号回归测试。
- [x] 5.4 明确阶段 deadline 为协作预算，等待不可取消的 DB/磁盘/转换/日志操作安全结束；协调器现在使用 `BATCH_PHASE_DEADLINE_SECONDS` 创建阶段预算，超时不会跨过图片屏障。
- [x] 5.5 小说阶段和批次摘要、`NOVEL_PHASE_DONE` 已补齐；最终 sink 排空失败纳入统一结果并已通过回归测试。

## 6. 文档、兼容性与集成验证

### 验证记录

已补充阶段顺序、deadline、禁用阶段上下文、初始化失败、信号停止、日志上下文、文件 sink 故障和阶段排空测试；完整测试套件共 62 项全部通过。

追加修复：小说阶段输出 `NOVEL_PHASE_DONE` 后独立执行日志排空检查，再输出 `BATCH_SUMMARY`；调度器契约测试已验证该顺序。

- [x] 6.1 更新 README：默认一次性两阶段、阶段标记、退出码、协作停止、404 保留、无变化行为和外部任务计划器绝对路径/起始目录/单实例配置。
- [x] 6.2 更新旧 scheduler_novel sleep、常驻轮询和 helper 测试，保留必要的 `run()` 兼容包装；默认入口和兼容 helper 均不再执行下一轮 sleep，crawler/共享线程池调用保持兼容。
- [x] 6.3 运行配置矩阵、阶段顺序、worker/callback 屏障、失败传播、日志 sink、停止和重复批次测试，以及 Python 编译检查。
- [x] 6.4 使用 `openspec validate strict-global-log-order --strict` 和 `openspec status --change strict-global-log-order` 校验规划，确认所有任务完成前不得宣称实现完成。
