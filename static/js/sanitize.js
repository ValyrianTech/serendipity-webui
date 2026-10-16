// HTML sanitization helpers for Serendipity Web UI
// DOMPurify is loaded globally via a non-module script in base.html.

export function escapeHtml(value) {
    // Escape &, <, >, " and ' so the result is safe in BOTH element-text
    // and quoted-attribute / inline-handler-string contexts.
    return String(value)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// Sanitize HTML produced by markdown/mermaid rendering to prevent stored XSS.
export function sanitizeHtml(html) {
    // Enable html, svg, and svgFilters profiles together because mermaid renders
    // diagrams as inline SVG. Mermaid SVG output uses foreignObject elements (which
    // carry HTML content) and style elements (covered by svgFilters) that must survive
    // sanitization for diagrams to render. The default html profile alone would strip
    // the SVG structure and break mermaid diagrams.
    if (typeof DOMPurify !== 'undefined') {
        return DOMPurify.sanitize(html, { USE_PROFILES: { html: true, svg: true, svgFilters: true } });
    }
    // Fall back to escaping so the sanitizer never becomes a silent no-op.
    return escapeHtml(html);
}
