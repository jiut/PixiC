<div align="center">

# PixiC V2.1.11

## **一个专注于 [PIXIV](https://www.pixiv.net/) 收藏插画和关注画师数据收集的Python爬虫**

</div>

</br>

**推荐旧版本用户升级到v2.1.7以上的版本**

</br>

## 1、功能

---

**当前支持功能**

- [x] **支持获取Chrome浏览器登录的Pixiv账户并生成cookie文件持久化**（新设备直接使用该文件即可）
- [x] **支持单图、多图、动图的原图下载**
- [x] **支持指定uid用户的关注画师作品的下载与数据收集**
- [x] **支持指定uid用户的公开/未公开收藏作品的下载与数据收集**
- [x] 支持周期性轮询以应对pixiv限制访问机制
- [x] 提供作品下载最低收藏数限制 --> [传送门](https://github.com/Coder-Sakura/PixiC/wiki/%E5%85%B3%E4%BA%8EPixiC%E9%85%8D%E7%BD%AE%E7%9A%84%E4%B8%89%E8%A8%80%E4%B8%A4%E8%AF%AD#%E5%87%A0%E7%A7%8D%E5%B8%B8%E8%A7%81%E5%9C%BA%E6%99%AF%E7%9A%84config%E6%96%87%E4%BB%B6%E9%85%8D%E7%BD%AE)
- [x] 提供pixiv作品稀有度划分 --> [传送门](https://github.com/Coder-Sakura/PixiC/wiki/%E5%85%B3%E4%BA%8EPixiC%E7%9A%84%E6%9D%82%E8%B0%88#%E5%85%B3%E4%BA%8E%E4%BD%9C%E5%93%81%E7%A8%80%E6%9C%89%E5%BA%A6%E5%88%92%E5%88%86)
- [x] 提供API以查询pid信息、随机插画接口 (含反代链接)
- [x] 可通过用户自定义cookie池(单个或多个cookie)
- [x] 可通过数据库开关以决定是否使用数据库收集数据
- [x] 日志功能

</br>

TODO list

- [ ] 日榜、周榜、月榜 
- [ ] 线程级任务状态通知用户（server酱、邮件等）

</br>

## 2、基本使用

---

+ 部署文档：  [WIKI](https://github.com/Coder-Sakura/PixiC/wiki) (推荐) 或 [Blog](http://mybot.top/blog/2020/06/24/pixic-bu-shu/)
+ 运行截图戳这：[运行截图](https://github.com/Coder-Sakura/PixiC/wiki/运行截图)
+ 注意事项戳这：[注意事项](https://github.com/Coder-Sakura/PixiC/wiki/%E6%B3%A8%E6%84%8F%E4%BA%8B%E9%A1%B9)

</br>

请参考<部署文档>完成环境搭建和依赖安装，接着运行

```
python scheduler.py
```

</br>

## 3、用途展示

---

### 随机pixiv插画

> PixiC API + [Mybot机器人](https://github.com/WriteCode-ChangeWorld/mybot)或其他qq机器人 可实现<请求API获取随机pixiv插画>

> Mybot现正处于开发阶段，欢迎star和fork

+ 查询pid

图片链接去除

+ 随机插画

图片链接去除



### 阅读3.0 订阅源[已失效]

订阅源链接: [蓝奏云](https://python.lanzout.com/b0bsanjmj)  密码: hsvs

图片链接去除



## 结尾的话

---

+ 希望可以提交`issue`和`pr`来帮助本项目进行完善，也欢迎`fork`和`star`本项目以对作者表示支持，感谢！
+ 本仓库仅用于学习与交流使用，因使用而产生的一切纠纷与原作者无关。
# 收藏小说

在 `v2.0/config.py` 中使用 `PIXIV_NOVEL_ENABLED` 开启小说收藏抓取，
`NOVEL_PATH` 指定独立目录（空字符串使用运行目录下的 `novel`）。
沿用账号 Cookie 和 `BOOKMARK_HIDE_ENABLE`。默认入口是一次性批次：先完成收藏图片阶段，
排空并输出 `IMAGE_PHASE_DONE` 后才初始化小说阶段；`BOOKMARK_CYCLE` 不再由入口常驻等待，
请交给外部任务计划器控制周期。在 `v2.0` 下运行 `python novel.py` 可单次抓取。

每部小说保存为 `<NOVEL_PATH>/<小说ID>/<小说ID>.epub`，包含封面、作者上传的内嵌图片、
引用的 Pixiv 插画（含多图页码），支持离线阅读、章节目录、分页和注音。
同目录保留 UTF-8 TXT 原文及 `metadata.json` 完整信息。不自动扩展抓取整个系列。
每批次遍历公开/私密收藏并检查更新，内容相同时不重复写文件；图片下载失败或引用图片不可访问时，
保留旧 EPUB 并在下轮重试，不将缺图版本记为成功。
每批次先校验数据库最近 20 部小说的 EPUB 文件并只输出汇总；随后只检查公开、私密收藏最新各 10 部。
收藏未变化时跳过详情抓取和 EPUB 重建，检测到新收藏后才全量同步收藏列表。
`SKIP_DB_EXISTS_NOVEL=True` 时，同步列表会跳过数据库已有小说并汇总数量；
最近 20 部的一致性检查会验证 EPUB 清单、阅读顺序、XML、图片和本地引用，异常时尝试修复。

启用 `DB_ENABLE` 后，小说任务自动创建独立的 `novel` 表和 `novel_unavailable` 不可访问状态表，并按 `nid` 更新元数据和 EPUB 文件路径。
已有 TXT 记录将在下次成功抓取时自动更新为 EPUB 路径，无需修改表结构。
文件全部保存成功才写入数据库，失败作品会在下一轮重试。关闭数据库也可保存小说。
图片或小说遇到已删除、私密或无权限作品时，仅记录不可访问状态，不删除数据库历史记录或本地文件。
已有数据库需要手动建表时，执行 `v2.0/doc/create_novel.sql` 和 `v2.0/doc/create_novel_unavailable.sql`（不要整体执行旧 `create.sql`，
其中含历史查询和删除示例）。

接口参考：[PixivBatchDownloader API.ts](https://github.com/xuejianxianzun/PixivBatchDownloader/blob/master/src/ts/API.ts)。

### 批次调度与退出码

推荐使用 Windows 任务计划器启动 `D:\Files\Programs\PixiC\.venv\Scripts\python.exe`，参数为
`D:\Files\Programs\PixiC\v2.0\scheduler.py`，起始目录设置为 `D:\Files\Programs\PixiC\v2.0`，
并选择“如果任务已在运行，则不启动新实例”。程序内仍有单实例锁，避免重复批次。

退出码：`0` 表示成功或无变化，`1` 表示抓取、下载、数据库或日志错误，`2` 表示配置/登录/初始化失败，
`3` 表示已有批次运行，`130` 表示收到 Ctrl+C 或 SIGTERM 协作停止。图片单项失败在日志和摘要中保留，
只要线程、资源和日志正常收尾，仍会继续小说阶段；阶段屏障或日志排空失败则跳过小说。
连接交互式终端运行时，小说阶段和批次摘要输出完成后会等待按 Enter 退出；任务计划器或日志重定向运行不会等待。
