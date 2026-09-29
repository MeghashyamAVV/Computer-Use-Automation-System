from __future__ import annotations

from dataclasses import dataclass, field

_EXTRACT_JS = r"""
() => {
  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return aria.trim();
    if (el.labels && el.labels.length) {
      return Array.from(el.labels).map(l => l.textContent.trim()).join(' ').trim();
    }
    const title = el.getAttribute('title');
    if (title) return title.trim();
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) return placeholder.trim();
    const text = (el.innerText || el.textContent || '').trim();
    return text.slice(0, 120);
  }

  function computedRole(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button') return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'password') return 'textbox';
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'td') return 'cell';
    if (tag === 'th') return 'columnheader';
    return null;
  }

  function cssPath(el) {
    // Best-effort structural fallback locator -- last resort only.
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      let selector = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (siblings.length > 1) {
          selector += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(selector);
      node = node.parentElement;
    }
    return parts.join(' > ');
  }

  const nodes = Array.from(document.querySelectorAll('a, button, input, select, textarea, [role], h1, h2, h3, td, th'));
  const out = [];
  for (const el of nodes) {
    const tag = el.tagName.toLowerCase();
    if ((tag === 'td' || tag === 'th') &&
        el.querySelector('table, form, input, select, textarea, button, a')) {
      // Layout/container cell (wraps a nested table or form), not a leaf
      // data cell -- skip it to avoid flooding the observation with noise
      // from table-based layout markup.
      continue;
    }
    const rect = el.getBoundingClientRect();
    const visible = rect.width > 0 && rect.height > 0;
    const role = computedRole(el);
    if (!role) continue;
    out.push({
      role,
      name: accessibleName(el),
      tag: el.tagName.toLowerCase(),
      value: 'value' in el ? String(el.value || '') : null,
      checked: 'checked' in el ? !!el.checked : null,
      visible,
      css_path: cssPath(el),
      href: el.getAttribute('href') || null,
    });
  }
  return out;
}
"""


@dataclass
class ObservedElement:
    index: int
    role: str
    name: str
    tag: str
    value: str | None
    visible: bool
    css_path: str
    href: str | None
    frame_path: list[str] = field(default_factory=list)  # e.g. ["Account balances"] for an iframe by title


@dataclass
class Observation:
    url: str
    title: str
    elements: list[ObservedElement]
    screenshot_path: str | None = None

    def to_llm_text(self, max_elements: int = 60) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}", "Interactive elements (index: role \"name\" [value]):"]
        for el in self.elements[:max_elements]:
            frame_note = f" (in frame: {' > '.join(el.frame_path)})" if el.frame_path else ""
            value_note = f" [value={el.value!r}]" if el.value else ""
            vis_note = "" if el.visible else " [hidden]"
            lines.append(f"  {el.index}: {el.role} \"{el.name}\"{value_note}{vis_note}{frame_note}")
        return "\n".join(lines)


def _frame_label(frame) -> str:
    # Prefer a human-legible label for the frame; falls back to its URL.
    try:
        name = frame.name
    except Exception:
        name = ""
    return name or frame.url


def observe(page) -> Observation:
    """Build an Observation from the live Playwright page (sync API), main
    frame first, then any child iframes, recursively."""
    elements: list[ObservedElement] = []
    idx = 0

    def walk(frame, frame_path: list[str]):
        nonlocal idx
        try:
            raw = frame.evaluate(_EXTRACT_JS)
        except Exception:
            raw = []
        for item in raw:
            elements.append(
                ObservedElement(
                    index=idx,
                    role=item["role"],
                    name=item["name"],
                    tag=item["tag"],
                    value=item.get("value"),
                    visible=item.get("visible", True),
                    css_path=item.get("css_path", ""),
                    href=item.get("href"),
                    frame_path=list(frame_path),
                )
            )
            idx += 1
        for child in frame.child_frames:
            walk(child, frame_path + [_frame_label(child)])

    walk(page.main_frame, [])

    return Observation(url=page.url, title=page.title(), elements=elements)
