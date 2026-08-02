(() => {
  // 示例题均经 neo4j_qa 实跑校验（knowledge_base + 非拒答），勿随意改写措辞。
  const EXAMPLES = [
    { category: "熊猫知识", text: "大熊猫是否具有冬眠习性？" },
    { category: "熊猫知识", text: "大熊猫福龙是在哪里出生的？" },
    { category: "熊猫知识", text: "大熊猫的气味标记方式是什么？" },
    { category: "熊猫知识", text: "大熊猫在野外发生冲突的主要原因是什么？" },
    { category: "熊猫谣言", text: "大熊猫是猫科动物吗？" },
    { category: "熊猫谣言", text: "大熊猫属于浣熊科吗？" },
    { category: "熊猫谣言", text: "大熊猫是濒危动物即将灭绝吗？" },
    { category: "熊猫资料", text: "和花的父母分别是谁？" },
    { category: "熊猫资料", text: "和花是什么时候出生的？" },
    { category: "熊猫资料", text: "萌兰的父母是谁？" },
    { category: "熊猫资料", text: "萌兰是什么时候出生的？" },
  ];

  /** 「全部」时每个栏目各取几条，避免列表过长且栏目失衡。 */
  const ALL_CATEGORY_QUOTA = 2;

  const el = {
    category: document.getElementById("category"),
    persona: document.getElementById("persona"),
    question: document.getElementById("question"),
    askBtn: document.getElementById("ask-btn"),
    status: document.getElementById("status"),
    result: document.getElementById("result"),
    answer: document.getElementById("answer"),
    hits: document.getElementById("hits"),
    sources: document.getElementById("sources"),
    badgeMode: document.getElementById("badge-mode"),
    badgeQuality: document.getElementById("badge-quality"),
    badgeCat: document.getElementById("badge-cat"),
    error: document.getElementById("error"),
    exampleButtons: document.getElementById("example-buttons"),
  };

  function basename(path) {
    if (!path) return "";
    const parts = String(path).replace(/\\/g, "/").split("/");
    return parts[parts.length - 1] || path;
  }

  function setLoading(loading) {
    el.askBtn.disabled = loading;
    el.status.textContent = loading ? "检索与生成中，请稍候…" : "";
  }

  function showError(msg) {
    el.error.textContent = msg || "请求失败";
    el.error.classList.remove("hidden");
  }

  function clearError() {
    el.error.textContent = "";
    el.error.classList.add("hidden");
  }

  const WRAPPER_TITLES = new Set([
    "全面回答",
    "全文回答",
    "简洁回答",
    "回答",
    "答案",
  ]);

  /** 模型偶发照抄提示词占位，不当作真实标题。 */
  const PLACEHOLDER_TITLES = new Set(["小节标题", "标题", "主题", "要点标题"]);

  /** 去掉表情、装饰符号与多余 Markdown，保留正式正文。 */
  function cleanAnswerText(raw) {
    let text = String(raw || "");
    text = text.replace(/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/gu, "");
    text = text.replace(/[✅❌⚠⚠️✔✖★☆●◆■□▪▫➤➔⇒→←↑↓•‣∙]/g, "");
    text = text.replace(/\*\*(.*?)\*\*/g, "$1");
    text = text.replace(/__(.*?)__/g, "$1");
    text = text.replace(/`([^`]+)`/g, "$1");
    text = text.replace(/^\s{0,3}#{1,6}\s*/gm, "");
    text = text.replace(/^\s*[-*]\s+/gm, "- ");
    text = text.replace(/[ \t]+\n/g, "\n");
    text = text.replace(/\n{3,}/g, "\n\n");
    return text.trim();
  }

  function isBullet(line) {
    return /^[-–—]\s+\S/.test(line) || /^\d+[\.、]\s+\S/.test(line);
  }

  function stripBullet(line) {
    return line.replace(/^[-–—]\s+/, "").replace(/^\d+[\.、]\s+/, "").trim();
  }

  /** 识别小节标题行，如「主要资金来源：」或「标题：正文」 */
  function parseHeadingLine(line) {
    const t = line.trim();
    if (!t || isBullet(t)) return null;

    const inline = t.match(/^(.{2,18}?)[：:]\s*(.+)$/);
    if (inline) {
      let title = inline[1].replace(/^\d+[.)、]\s*/, "").trim();
      let rest = inline[2].trim();
      if (PLACEHOLDER_TITLES.has(title) && rest && !isBullet(rest) && rest.length <= 18) {
        title = rest;
        rest = "";
      }
      if (
        title &&
        !WRAPPER_TITLES.has(title) &&
        !PLACEHOLDER_TITLES.has(title) &&
        !/[。？！?!]/.test(title)
      ) {
        return { title, rest };
      }
    }

    const alone = t.match(/^(.{2,16}?)[：:]\s*$/);
    if (alone) {
      const title = alone[1].replace(/^\d+[.)、]\s*/, "").trim();
      if (
        title &&
        !WRAPPER_TITLES.has(title) &&
        !PLACEHOLDER_TITLES.has(title) &&
        !/[。？！?!，,]/.test(title)
      ) {
        return { title, rest: "" };
      }
    }
    return null;
  }

  function extractCoreBody(cleaned) {
    let body = cleaned
      .split(/\n(?=\s*(?:\d+\)\s*)?(?:依据来源|参考来源|来源)\s*[:：]?)/)[0]
      .trim();
    body = body.replace(/^\s*\d+\)\s*(?:全面回答|全文回答|简洁回答|回答)\s*[:：]?\s*\n?/i, "");
    body = body.replace(/^\s*(?:全面回答|全文回答)\s*[:：]?\s*\n?/i, "");
    return body.trim();
  }

  /** 将回答拆成：开篇概要 + 若干主题模块。 */
  function parseAnswerModules(text) {
    const cleaned = cleanAnswerText(text);
    if (!cleaned) {
      return { lead: "暂无回答", modules: [] };
    }
    const body = extractCoreBody(cleaned);
    const lines = body.split("\n").map((x) => x.trim()).filter(Boolean);

    const modules = [];
    const leadParts = [];
    let current = null;
    let inModules = false;

    function ensureModule(title) {
      current = { title: title || "", paragraphs: [], bullets: [] };
      modules.push(current);
      inModules = true;
      return current;
    }

    lines.forEach((line) => {
      const heading = parseHeadingLine(line);
      if (heading) {
        const mod = ensureModule(heading.title);
        if (heading.rest) {
          if (isBullet(heading.rest)) mod.bullets.push(stripBullet(heading.rest));
          else mod.paragraphs.push(heading.rest);
        }
        return;
      }

      if (isBullet(line)) {
        if (!current) ensureModule("要点");
        current.bullets.push(stripBullet(line));
        return;
      }

      if (!inModules) {
        leadParts.push(line);
        return;
      }

      if (!current) ensureModule("");
      current.paragraphs.push(line);
    });

    if (!modules.length) {
      const bullets = lines.filter(isBullet).map(stripBullet);
      const paras = lines.filter((x) => !isBullet(x));
      if (bullets.length >= 2) {
        return {
          lead: paras.join("") || "",
          modules: [{ title: "要点", paragraphs: [], bullets }],
        };
      }
      return { lead: body, modules: [] };
    }

    return { lead: leadParts.join(""), modules };
  }

  function appendTextBlocks(parent, paragraphs, bullets) {
    (paragraphs || []).forEach((text) => {
      if (!text) return;
      const p = document.createElement("p");
      p.className = "module-text";
      p.textContent = text;
      parent.appendChild(p);
    });
    if (bullets && bullets.length) {
      const ul = document.createElement("ul");
      ul.className = "module-list";
      bullets.forEach((item) => {
        const li = document.createElement("li");
        li.textContent = item;
        ul.appendChild(li);
      });
      parent.appendChild(ul);
    }
  }

  function renderAnswerBody(text) {
    el.answer.innerHTML = "";
    const { lead, modules } = parseAnswerModules(text);

    if (lead) {
      const leadEl = document.createElement("div");
      leadEl.className = "answer-lead";
      const label = document.createElement("span");
      label.className = "answer-lead-label";
      label.textContent = "概要";
      const p = document.createElement("p");
      p.textContent = lead;
      leadEl.appendChild(label);
      leadEl.appendChild(p);
      el.answer.appendChild(leadEl);
    }

    if (!modules.length) {
      if (!lead) {
        const empty = document.createElement("p");
        empty.className = "empty";
        empty.textContent = "暂无回答";
        el.answer.appendChild(empty);
      }
      return;
    }

    const grid = document.createElement("div");
    grid.className = "answer-modules";
    modules.forEach((mod, idx) => {
      const card = document.createElement("section");
      card.className = "answer-module";

      const head = document.createElement("header");
      head.className = "module-head";
      const idxEl = document.createElement("span");
      idxEl.className = "module-index";
      idxEl.textContent = String(idx + 1).padStart(2, "0");
      head.appendChild(idxEl);
      card.appendChild(head);

      const body = document.createElement("div");
      body.className = "module-body";
      if (mod.title && !PLACEHOLDER_TITLES.has(mod.title)) {
        const title = document.createElement("h3");
        title.className = "module-title";
        title.textContent = mod.title;
        body.appendChild(title);
      }
      appendTextBlocks(body, mod.paragraphs, mod.bullets);
      card.appendChild(body);
      grid.appendChild(card);
    });
    el.answer.appendChild(grid);
  }

  function renderHits(evidences) {
    el.hits.innerHTML = "";
    const rows = Array.isArray(evidences) ? evidences.slice(0, 8) : [];
    if (!rows.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "暂无命中关系";
      el.hits.appendChild(li);
      return;
    }
    rows.forEach((row) => {
      const li = document.createElement("li");
      const s = row.subject || "";
      const p = row.predicate || "";
      const o = row.object || "";
      li.innerHTML = "";
      li.appendChild(document.createTextNode(s + " "));
      const pred = document.createElement("span");
      pred.className = "pred";
      pred.textContent = p;
      li.appendChild(pred);
      li.appendChild(document.createTextNode(" " + o));
      el.hits.appendChild(li);
    });
  }

  function renderSources(evidences) {
    el.sources.innerHTML = "";
    const seen = new Set();
    const names = [];
    (Array.isArray(evidences) ? evidences : []).forEach((row) => {
      const name = basename(row.source_file || "");
      if (name && !seen.has(name)) {
        seen.add(name);
        names.push(name);
      }
    });
    if (!names.length) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "暂无来源文档";
      el.sources.appendChild(li);
      return;
    }
    names.slice(0, 10).forEach((name) => {
      const li = document.createElement("li");
      li.textContent = name;
      el.sources.appendChild(li);
    });
  }

  function renderResult(data) {
    el.result.classList.remove("hidden");
    renderAnswerBody(data.answer || "");

    const mode = data.answer_mode || "unknown";
    el.badgeMode.textContent =
      mode === "knowledge_base" ? "知识库作答" : mode === "llm_fallback" ? "通用知识兜底" : mode;

    const quality = data.quality || {};
    const score = quality.score_0_100;
    const level = quality.level || "";
    el.badgeQuality.textContent =
      score === undefined || score === null || score === ""
        ? "质量分暂无"
        : `质量 ${score}${level ? ` · ${level}` : ""}`;

    el.badgeCat.textContent = `栏目 ${data.category || "全部"}`;

    renderHits(data.evidences || []);
    renderSources(data.evidences || []);
  }

  async function ask() {
    const question = (el.question.value || "").trim();
    if (!question) {
      showError("请先输入问题");
      return;
    }
    clearError();
    setLoading(true);
    try {
      const resp = await fetch("/api/ask", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question,
          category: el.category.value,
          persona: el.persona.value,
          top_k: 20,
          strict_mode: true,
        }),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const detail = data.detail;
        const msg = Array.isArray(detail)
          ? detail.map((x) => x.msg || JSON.stringify(x)).join("; ")
          : detail || `HTTP ${resp.status}`;
        throw new Error(msg);
      }
      if (data.error) {
        showError(String(data.error));
      }
      renderResult(data);
    } catch (err) {
      el.result.classList.add("hidden");
      showError(err && err.message ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  function examplesForCategory(category) {
    const cat = (category || "全部").trim();
    if (cat && cat !== "全部") {
      return EXAMPLES.filter((item) => item.category === cat);
    }
    const picked = [];
    const counts = {};
    EXAMPLES.forEach((item) => {
      const n = counts[item.category] || 0;
      if (n < ALL_CATEGORY_QUOTA) {
        picked.push(item);
        counts[item.category] = n + 1;
      }
    });
    return picked;
  }

  function renderExamples() {
    el.exampleButtons.innerHTML = "";
    const items = examplesForCategory(el.category.value);
    if (!items.length) {
      const tip = document.createElement("span");
      tip.className = "status";
      tip.textContent = "当前栏目暂无示例问题";
      el.exampleButtons.appendChild(tip);
      return;
    }
    items.forEach((item) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = item.text;
      btn.addEventListener("click", () => {
        el.category.value = item.category;
        el.question.value = item.text;
        renderExamples();
        clearError();
        ask();
      });
      el.exampleButtons.appendChild(btn);
    });
  }

  el.askBtn.addEventListener("click", ask);
  el.category.addEventListener("change", renderExamples);
  el.question.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) {
      ev.preventDefault();
      ask();
    }
  });

  renderExamples();
})();

