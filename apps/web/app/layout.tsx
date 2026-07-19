import Link from 'next/link'
import './globals.css'

export const metadata = {
  title: 'Kalshi Bot Ops',
  description: 'Trading worker operations console',
}

const navItems = [
  { href: '/', label: 'Dashboard', icon: 'D' },
  { href: '/orders', label: 'Orders', icon: 'O' },
  { href: '/fills', label: 'Fills', icon: 'F' },
  { href: '/markets', label: 'Markets', icon: 'M' },
]

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en">
      <body>
        <div className="appFrame">
          <aside className="sidebar">
            <Link className="brand" href="/">
              <span className="brandMark">K</span>
              <span>KALSHI_BOT</span>
            </Link>
            <nav className="nav">
              {navItems.map((item) => (
                <Link key={item.href} className="navLink" href={item.href}>
                  <span className="navIcon">{item.icon}</span>
                  {item.label}
                </Link>
              ))}
            </nav>
            <div className="sidebarStatus">
              <div className="statusLine">
                <span className="muted">Status</span>
                <span className="liveStatus">
                  <span className="pulseDot" />
                  running
                </span>
              </div>
              <button className="killSwitch" type="button">Kill Switch</button>
            </div>
          </aside>
          <main className="shell">{children}</main>
        </div>
      </body>
    </html>
  )
}
