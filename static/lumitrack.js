window.LumiTrack = {
  money(value) {
    const amount = Math.round(Number(value || 0));
    return `${amount.toLocaleString("ko-KR")}\uC6D0`;
  },

  percent(value) {
    const percent = Number(value || 0);
    return `${percent.toFixed(1)}%`;
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
                if (suffix === "\uC6D0") return `${label}: ${LumiTrack.money(value)}`;
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
                if (suffix === "\uC6D0") return Number(value).toLocaleString("ko-KR");
                if (suffix === "%") return `${value}%`;
                return value;
              }
            }
          }
        }
      }
    });
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
        LumiTrack.toast("수집을 백그라운드로 시작했어요.", "success");
      } catch (error) {
        console.error(error);
        LumiTrack.toast("수집 시작 요청이 실패했어요.", "danger");
      } finally {
        if (button) {
          button.disabled = false;
          button.textContent = originalText;
        }
      }
    });
  });
});
