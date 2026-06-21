window.LumiTrack = {
  _pollTimer: null,
  _polling: false,
  _lastRunning: false,
  _refreshingMarket: false,

  money(value) {
    const amount = Math.round(Number(value || 0));
    return `${amount.toLocaleString("ko-KR")}원`;
  },

  percent(value) {
    const percent = Number(value || 0);
    return `${percent.toFixed(1)}%`;
  },

  number(value) {
    return Math.round(Number(value || 0)).toLocaleString("ko-KR");
  },

  toast(message, tone = "info") {
    let element = document.getElementById("lumitrackToast");
    if (!element) {
      element = document.createElement("div");
      element.id = "lumitrackToast";
      element.className = "toast";
      document.body.appendChild(element);
    }
    element.className = `toast show ${tone}`;
    element.innerHTML = `
      <strong>${message}</strong>
      <a href="/status">수집 상태 보기</a>
    `;
    window.clearTimeout(element._hideTimer);
    element._hideTimer = window.setTimeout(() => {
      element.classList.remove("show");
    }, 4200);
  },

  renderBar(canvasId, payload, label, suffix, color) {
    const element = document.getElementById(canvasId);
    if (!element || !window.Chart) return;
    if (element._lumitrackChart) {
      element._lumitrackChart.destroy();
    }

    const data = payload || { labels: [], values: [] };
    const gradient = element.getContext("2d").createLinearGradient(0, 0, 0, 260);
    gradient.addColorStop(0, color);
    gradient.addColorStop(1, "rgba(49, 130, 246, 0.20)");

    element._lumitrackChart = new Chart(element, {
      type: "bar",
      data: {
        labels: data.labels,
        datasets: [{
          label,
          data: data.values,
          backgroundColor: gradient,
          borderRadius: 12,
          maxBarThickness: 42
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        resizeDelay: 120,
        animation: {
          duration: 700,
          easing: "easeOutQuart"
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#101828",
            padding: 12,
            callbacks: {
              label(context) {
                const value = Number(context.raw || 0);
                if (suffix === "원") return `${label}: ${LumiTrack.money(value)}`;
                if (suffix === "%") return `${label}: ${LumiTrack.percent(value)}`;
                return `${label}: ${value.toLocaleString("ko-KR")}${suffix || ""}`;
              }
            }
          }
        },
        scales: {
          x: {
            grid: { display: false },
            ticks: {
              color: "#667085",
              font: { weight: 700 },
              maxRotation: 30,
              minRotation: 0
            }
          },
          y: {
            beginAtZero: true,
            grid: { color: "rgba(102, 112, 133, 0.12)" },
            ticks: {
              color: "#667085",
              callback(value) {
                if (suffix === "원") return Number(value).toLocaleString("ko-KR");
                if (suffix === "%") return `${value}%`;
                return value;
              }
            }
          }
        }
      }
    });
  },

  jobIsRunning(job, runningFlag) {
    return Boolean(
      runningFlag ||
      (job && (job.status === "running" || job.status === "starting"))
    );
  },

  progressParts(job) {
    const progress = (job && job.progress) || {};
    const completed = Number(progress.completed || 0);
    const total = Math.max(Number(progress.total || 1), 1);
    const percent = Math.max(0, Math.min(100, (completed / total) * 100));
    return {
      progress,
      completed,
      total,
      percent
    };
  },

  updateGlobalJob(payload) {
    const job = payload && payload.job;
    const running = this.jobIsRunning(job, payload && payload.running);
    const strip = document.getElementById("globalJobStrip");
    if (!strip) return;

    if (!job || !running) {
      strip.classList.add("hidden");
      if (this._lastRunning && job && job.status !== "running") {
        const label = job.status === "success" || job.status === "partial_success"
          ? "수집이 완료됐어. 최신 데이터로 바로 적용할게."
          : "수집 작업이 끝났어. 상태 화면에서 결과를 확인해줘.";
        this.toast(label, job.status === "failed" ? "danger" : "success");
        this.reloadOnceAfterFinishedJob(job);
      }
      this._lastRunning = false;
      return;
    }

    const { progress, completed, total, percent } = this.progressParts(job);
    const currentStore = progress.current_store || "수집 중";
    const currentDate = progress.current_date ? ` · ${progress.current_date}` : "";
    const failed = Number(progress.failed || 0);
    const slots = Number(progress.slots || 0);

    strip.classList.remove("hidden");
    if (job.job_id) {
      sessionStorage.setItem("lumitrackActiveJobId", job.job_id);
    }
    this.setText("globalJobTitle", job.label || "수집 작업");
    this.setText(
      "globalJobDetail",
      `${currentStore}${currentDate} · ${this.number(completed)} / ${this.number(total)} · 슬롯 ${this.number(slots)} · 실패 ${this.number(failed)}`
    );
    this.setWidth("globalJobProgress", percent);
    this._lastRunning = true;
  },

  reloadOnceAfterFinishedJob(job) {
    if (!job || !job.job_id) return;
    const finalStatuses = new Set(["success", "partial_success", "failed", "stopped"]);
    if (!finalStatuses.has(job.status)) return;

    const activeJobId = sessionStorage.getItem("lumitrackActiveJobId");
    const reloadKey = `lumitrackReloadedJob:${job.job_id}`;
    if (activeJobId !== job.job_id || sessionStorage.getItem(reloadKey)) return;

    sessionStorage.setItem(reloadKey, "1");
    sessionStorage.removeItem("lumitrackActiveJobId");
    window.setTimeout(() => {
      this.refreshMarketData({ reload: true, quiet: true });
    }, 300);
  },

  hardReload() {
    const url = new URL(window.location.href);
    url.searchParams.set("fresh", Date.now().toString());
    window.location.replace(url.toString());
  },

  async refreshMarketData({ reload = true, quiet = false } = {}) {
    if (this._refreshingMarket) return;
    this._refreshingMarket = true;
    document.querySelectorAll("[data-refresh-market]").forEach((button) => {
      button.disabled = true;
      button.dataset.originalText = button.textContent;
      button.textContent = "적용 중";
    });

    try {
      const response = await fetch(`/api/market/refresh?_=${Date.now()}`, {
        method: "POST",
        cache: "no-store",
        credentials: "same-origin"
      });
      if (!response.ok) throw new Error(`refresh ${response.status}`);
      const payload = await response.json();
      const summary = payload.summary || {};
      const detail = summary.latest_crawled_at
        ? `최신 ${summary.latest_crawled_at} · 7일 슬롯 ${this.number(summary.visible_7_slots || 0)}개`
        : `7일 슬롯 ${this.number(summary.visible_7_slots || 0)}개`;
      if (!quiet) this.toast(`최신 수집 데이터를 적용했어. ${detail}`, "success");
      if (reload) {
        window.setTimeout(() => this.hardReload(), quiet ? 150 : 650);
      }
    } catch (error) {
      console.error(error);
      this.toast("최신 데이터 적용에 실패했어.", "danger");
    } finally {
      this._refreshingMarket = false;
      document.querySelectorAll("[data-refresh-market]").forEach((button) => {
        button.disabled = false;
        button.textContent = button.dataset.originalText || "최신 데이터 적용";
      });
    }
  },

  updateStatusPage(payload) {
    const job = payload && payload.job;
    if (!document.getElementById("statusLiveState") && !document.getElementById("liveLogBox")) {
      return;
    }

    const running = this.jobIsRunning(job, payload && payload.running);
    const state = job ? job.status : "idle";
    const { progress, completed, total, percent } = this.progressParts(job);
    const currentStore = progress.current_store || (running ? "수집 중" : "-");
    const currentDate = progress.current_date || "";

    this.setText("statusLiveState", state);
    this.setText("statusLiveTitle", job ? (job.label || "수집 작업") : "대기 중");
    this.setText("statusLiveDetail", `${currentStore} ${currentDate}`.trim() || "-");
    this.setWidth("statusLiveProgress", percent);
    this.setText("statusLivePct", this.percent(percent));
    this.setText("statusLiveCompleted", `${this.number(completed)} / ${this.number(total)}`);
    this.setText("statusLiveSuccess", this.number(progress.success || 0));
    this.setText("statusLiveFailed", this.number(progress.failed || 0));
    this.setText("statusLiveSlots", this.number(progress.slots || 0));
    this.setText("statusLiveError", job && job.error ? job.error : "");

    if (typeof payload.log === "string") {
      this.setText("liveLogBox", payload.log || "아직 로그가 없습니다.");
    }
  },

  setText(id, value) {
    const element = document.getElementById(id);
    if (element) element.textContent = value;
  },

  setWidth(id, percent) {
    const element = document.getElementById(id);
    if (element) element.style.width = `${Math.max(0, Math.min(100, percent))}%`;
  },

  async pollCrawlStatus() {
    if (this._polling) return;
    this._polling = true;
    const includeLog = document.getElementById("liveLogBox") ? 1 : 0;

    try {
      const response = await fetch(`/api/crawl/status?log=${includeLog}&_=${Date.now()}`, {
        cache: "no-store",
        credentials: "same-origin"
      });
      if (!response.ok) throw new Error(`status ${response.status}`);
      const payload = await response.json();
      this.updateGlobalJob(payload);
      this.updateStatusPage(payload);
      if (payload.market_refreshed && !this.jobIsRunning(payload.job, payload.running)) {
        this.toast("수집 완료 데이터가 적용됐어.", "success");
      }
      const running = this.jobIsRunning(payload.job, payload.running);
      this.schedulePoll(running ? 1800 : 8000);
    } catch (error) {
      console.warn("crawl status poll failed", error);
      this.schedulePoll(10000);
    } finally {
      this._polling = false;
    }
  },

  schedulePoll(delayMs) {
    window.clearTimeout(this._pollTimer);
    this._pollTimer = window.setTimeout(() => this.pollCrawlStatus(), delayMs);
  },

  startStatusPolling(delayMs = 400) {
    this.schedulePoll(delayMs);
  }
};

document.addEventListener("DOMContentLoaded", () => {
  const overlay = document.getElementById("loadingOverlay");
  if (overlay) overlay.classList.remove("show");

  document.querySelectorAll("form[data-loading]").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = form.querySelector("button[type='submit']");
      const originalText = button ? button.textContent : "";
      if (button) {
        button.disabled = true;
        button.textContent = "수집 시작 중";
      }
      try {
        await fetch(form.action, {
          method: form.method || "POST",
          body: new FormData(form),
          redirect: "manual",
          credentials: "same-origin"
        });
        LumiTrack.toast("수집을 백그라운드에서 시작했어.", "success");
        LumiTrack.startStatusPolling(250);
      } catch (error) {
        console.error(error);
        LumiTrack.toast("수집 시작 요청에 실패했어.", "danger");
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = originalText;
        }
      }
    });
  });

  document.querySelectorAll("[data-refresh-market]").forEach((button) => {
    button.addEventListener("click", () => {
      LumiTrack.refreshMarketData({ reload: true });
    });
  });

  LumiTrack.startStatusPolling();
});
