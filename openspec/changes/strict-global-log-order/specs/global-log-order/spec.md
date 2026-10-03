# Spec Delta

## Purpose

为默认一次性批处理提供可验证的阶段顺序，使图片抓取、工作线程、回调及其全部日志先完成，再开始小说抓取和小说日志输出，并明确失败、无变化、重复运行及协作停止时的行为。

## ADDED Requirements

### Requirement: One-shot phase ordering
The default scheduler SHALL execute at most one image pass followed by at most one novel pass in a single process, without an ordering-mode option or internal cycle sleep. It SHALL acquire a process-wide single-instance lock before login or phase initialization, exit with code 3 when the lock is occupied, and SHALL NOT start another image phase in that process.

#### Scenario: Image phase completes before novel phase
- **WHEN** the default scheduler starts with both image and novel tasks enabled
- **THEN** the novel phase starts only after the image completion barrier and marker, and the process exits after the novel phase

#### Scenario: No internal polling
- **WHEN** a batch finishes
- **THEN** it reports its final status and exits without emitting a sleep message or waiting for another cycle

### Requirement: Task configuration
The batch SHALL honor image and novel enable flags. A disabled image phase SHALL still produce a drained completion boundary before enabled novel work. Disabled phases SHALL perform no initialization or fetching. Configuration enabling crawler or API services SHALL be rejected before phase initialization.

#### Scenario: Only novels enabled
- **WHEN** images are disabled and novels are enabled
- **THEN** the batch emits a disabled-image summary and drained image completion marker before novel initialization

#### Scenario: Only images enabled
- **WHEN** images are enabled and novels are disabled
- **THEN** the batch finishes the image boundary and exits without importing or constructing a novel task

#### Scenario: All tasks disabled
- **WHEN** images and novels are disabled and no conflicting service is enabled
- **THEN** the batch performs no login or fetching, emits its boundaries and disabled-task summary, and exits successfully

#### Scenario: Conflicting services enabled
- **WHEN** a crawler or API service is enabled
- **THEN** the batch reports a configuration error without starting either phase

### Requirement: Update outcome distinction
Update checks SHALL distinguish changed, unchanged and error outcomes, validating every enabled public or private list before deciding. Unchanged SHALL mean a successful check with no new candidates; network, authentication, permission and database failures SHALL NOT be reported as unchanged.

#### Scenario: No-change image phase
- **WHEN** consistency checks succeed and the image update check reports unchanged
- **THEN** the phase returns completed with an unchanged flag, emits its summary and completion marker, and the novel phase may start once

#### Scenario: Private list check fails
- **WHEN** a public list contains new candidates but an enabled private list cannot be checked
- **THEN** the phase reports a failure rather than a changed or unchanged result

### Requirement: Complete image output barrier
The scheduler SHALL emit `IMAGE_PHASE_DONE` only for a completed or disabled image phase after all its submitted tasks, callbacks, worker threads, summary and pending output have finished. The marker SHALL also be drained before novel initialization. A timed wait returning SHALL NOT establish completion while a worker remains alive.

#### Scenario: Worker output is drained
- **WHEN** image paging ends while a worker or callback is still active
- **THEN** its completion and final output precede the image summary and completion marker

#### Scenario: Worker exceeds cooperative budget
- **WHEN** an active image task outlasts the phase budget
- **THEN** the batch stops adding work, reports a deadline failure after cleanup, and never starts novels or emits a successful image completion marker

### Requirement: Ordered novel output
The scheduler SHALL emit no novel initialization, database, consistency, progress, download or summary record before the drained image completion marker. Image progress SHALL remain live, and existing terminal skip aggregation and full file records SHALL be preserved. Novel records SHALL retain their output order within each sink.

#### Scenario: Live image progress
- **WHEN** image work is active
- **THEN** its progress is output immediately while novel work has not been initialized

#### Scenario: Novel initialization ordering
- **WHEN** a novel task and its database are initialized
- **THEN** every associated record appears after `IMAGE_PHASE_DONE` in each healthy sink

### Requirement: Single-pass result contract
Each phase SHALL return completed, failed or stopped with counts, an unchanged flag, `barrier_passed`, and an error or stop reason when applicable. A completed phase MAY contain recoverable item errors only when `barrier_passed` is true. It SHALL perform one pass, allow finite retry or rate-limit waits, and SHALL NOT enter another polling cycle. Batch exit codes SHALL be 0 for success, 1 for phase or output failure, 2 for configuration/login/initialization failure, 3 when another batch owns the lock, and 130 for cooperative interruption.

#### Scenario: Phase returns after one pass
- **WHEN** a phase processes all configured bookmark pages or reaches a no-change decision
- **THEN** it returns a result to the coordinator without a next-cycle sleep

#### Scenario: Completed phase with item errors
- **WHEN** some works fail after other works were saved but all phase workers, resources and healthy logs finish
- **THEN** its result has `status=completed` and `barrier_passed=true`, retains completed counts and error details, and the batch exits with code 1 after any allowed later phase

### Requirement: Initialization failure reporting
Configuration, login and phase initialization failures SHALL produce a non-success summary identifying the failing step and exit code 2. An initializer terminating with a success-valued process exit SHALL NOT cause a failed initialization to be reported as a successful batch.

#### Scenario: Database initialization fails
- **WHEN** a required phase database cannot initialize
- **THEN** the batch reports initialization failure, performs available cleanup and does not start any later phase

### Requirement: Failure barrier
The scheduler SHALL decide whether to start novels from the image completion barrier, not from recoverable item errors. If image workers, resources and healthy logs finish and the barrier is established, novels SHALL start even when individual image downloads, database writes or repairs failed; the final batch status SHALL remain non-zero. If the image phase cannot establish the barrier, is stopped, or its output fails, the scheduler SHALL skip novels, wait for active image workers and callbacks to end, drain healthy output and report a non-zero final status. A failed novel phase SHALL likewise drain healthy output and exit non-zero. Previously completed files and database records SHALL be preserved; incomplete work SHALL NOT be marked successfully archived.

#### Scenario: Recoverable image item failure
- **WHEN** a required image download, consistency repair or database write fails for some items but all workers, resources and healthy logs finish
- **THEN** the batch starts novels after `IMAGE_PHASE_DONE`, preserves the item failures in its final result, and exits non-zero

#### Scenario: Fatal image boundary failure
- **WHEN** image initialization, worker cleanup, output drainage or a cooperative stop prevents the completion barrier
- **THEN** the batch reports image failure after available cleanup and skips novels

#### Scenario: Novel phase failure
- **WHEN** the image phase completed and the novel phase returns `failed`
- **THEN** the batch drains healthy novel output and exits non-zero without rolling back completed EPUBs

### Requirement: Archive success evidence
A phase SHALL count success only after required downloads and enabled database writes succeed. Consistency checks SHALL report counts and distinguish an empty result from a query failure. Intentionally filtered or already archived work and disabled database checks SHALL be counted as skips without causing failure.

#### Scenario: Download metadata exists but a required file failed
- **WHEN** metadata was retrieved but a required image download failed
- **THEN** the work is counted as failed and is not written as a successful archive

#### Scenario: Consistency query fails
- **WHEN** the recent-record query fails
- **THEN** the phase reports a failure instead of a successful empty check

#### Scenario: Database disabled
- **WHEN** database support is disabled
- **THEN** consistency checking is skipped and successfully written files can still count as successful downloads

### Requirement: Unavailable archive preservation
Confirmed work-detail 404 or unavailable results SHALL be counted separately from transient failures, SHALL NOT trigger detail retries, and SHALL preserve existing files and database records. Required missing embedded images in an otherwise available novel SHALL remain a generation failure.

#### Scenario: Archived work becomes unavailable
- **WHEN** a consistency check or work-detail request confirms remote unavailability
- **THEN** the phase counts an unavailable work and preserves its historical archive without retrying that detail request

#### Scenario: Novel embedded image unavailable
- **WHEN** an available novel requires an embedded image that cannot be obtained
- **THEN** EPUB generation reports a failure instead of treating the novel as successfully skipped

### Requirement: Output failure reporting
The batch SHALL detect output-write errors separately from queue drainage. An image-output error SHALL prevent novel startup even if the output queue was drained. It SHALL report failure through available healthy output and best-effort stderr; completeness SHALL be guaranteed only for healthy sinks.

#### Scenario: File writer fails
- **WHEN** the image file-log sink reports a write error while consuming its queue
- **THEN** the batch records that error, finishes draining available output and does not start novels

### Requirement: Graceful termination
On a cooperative stop request, the batch SHALL stop new work, allow active operations to end safely, wait for workers and drain healthy logs, then report stopped with code 130. A phase budget expiry SHALL report failed with reason deadline and code 1. Neither SHALL guarantee hard-time termination. Forced process termination SHALL be outside the graceful-cleanup guarantee.

#### Scenario: Stop requested during image phase
- **WHEN** a cooperative stop request arrives during images
- **THEN** new work stops at cooperative boundaries, active workers finish safely, novels are skipped, and the summary identifies the interrupted image phase

#### Scenario: Stop requested after image phase
- **WHEN** a cooperative stop request arrives after images and before novel completion
- **THEN** novel startup or further novel work stops, active work and healthy output are cleaned up, and the batch exits with code 130

#### Scenario: Operation cannot be cancelled
- **WHEN** an active disk, database, conversion or output operation does not support cancellation
- **THEN** cleanup waits for it to end, without claiming a hard exit deadline or moving to the next phase

#### Scenario: Windows process forcibly terminated
- **WHEN** the process is killed through task-manager or task-scheduler termination
- **THEN** completed archives remain usable but final logs, cleanup and partial writes are not guaranteed

### Requirement: Stage log boundary
Records SHALL carry batch and phase context: configuration and login belong to preamble; image initialization, shared downloader/database/checker activity, workers, callbacks and summary belong to images; novel initialization and all novel work belong to novels. Final batch records SHALL follow phase cleanup. No image-phase record SHALL appear after successful image completion.

#### Scenario: Shared worker logger
- **WHEN** an image worker calls the shared downloader or database logger
- **THEN** its record carries image context and precedes the image completion marker

### Requirement: Repeated batch behavior
Each new batch SHALL check recent consistency records and latest bookmark candidates using the existing limits, skip paging when unchanged, and traverse complete enabled image lists when changed, resetting pagination per list. It SHALL use archive skips to avoid redundant downloads and SHALL NOT depend on a previous process's cycle counter. Repeated execution SHALL preserve completed archives.

#### Scenario: Changed image collection exceeds old cycle cap
- **WHEN** a new batch detects changes in an image list larger than the old cycle limit
- **THEN** it traverses the complete list without truncating at that limit, while skipping existing archives

#### Scenario: Public paging fails before private paging
- **WHEN** public paging fails after advancing its offset and enabled private paging proceeds
- **THEN** private paging begins at offset zero and the public failure remains in the phase result

#### Scenario: Unchanged novel collection
- **WHEN** a new batch's recent consistency check succeeds and latest novel candidates are unchanged
- **THEN** it reports a completed unchanged novel phase without validating every bookmark individually
