// Root layout — the reading frame (§4: single column, 720px max, tabs are
// the only navigation; §8: landmarks + keyboard-first).
import "./globals.css";

export const metadata = {
  title: "Macro Signal",
  description: "The weekly verdicts, read from signals.db",
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <body>
        <main>
          <nav className="tabs" aria-label="Screens">
            <a href="/">This week</a>
            <a href="/journal">Journal &amp; system</a>
          </nav>
          {children}
        </main>
      </body>
    </html>
  );
}
