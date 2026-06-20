window.LumiTrack = {
  money(value) {
    const amount = Math.round(Number(value || 0));
    return `${amount.toLocaleString("ko-KR")}원`;
  },

  percent(value) {
    const percent = Number(value || 0);
    return `${percent.toFixed(1)}%`;
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
  }
};

document.addEventListener("DOMContentLoaded", () => {
  const overlay = document.getElementById("loadingOverlay");
  document.querySelectorAll("form[data-loading]").forEach((form) => {
    form.addEventListener("submit", () => {
      if (overlay) overlay.classList.add("show");
    });
  });
});
