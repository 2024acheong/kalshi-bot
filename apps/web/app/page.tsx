const sections = [
  {
    title: "Partner A",
    items: ["Own dashboard shell", "Build API integration", "Ship positions and runs views"],
  },
  {
    title: "Partner B",
    items: ["Own worker runtime", "Build Kalshi ingestion", "Define shared market schema"],
  },
];

export default function HomePage() {
  return (
    <main style={{ fontFamily: "ui-sans-serif, system-ui", padding: 32, maxWidth: 960, margin: "0 auto" }}>
      <h1 style={{ fontSize: 40, marginBottom: 12 }}>Kalshi Trading System</h1>
      <p style={{ fontSize: 18, lineHeight: 1.5, marginBottom: 24 }}>
        Start with one end-to-end slice: ingest one market, normalize it, persist it, expose it through the API,
        and render it here.
      </p>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 16 }}>
        {sections.map((section) => (
          <article key={section.title} style={{ border: "1px solid #d4d4d8", borderRadius: 12, padding: 20 }}>
            <h2 style={{ marginTop: 0 }}>{section.title}</h2>
            <ul style={{ paddingLeft: 20, marginBottom: 0 }}>
              {section.items.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </article>
        ))}
      </section>
    </main>
  );
}
