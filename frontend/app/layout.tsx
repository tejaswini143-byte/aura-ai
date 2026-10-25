import AppShell from "./components/AppShell";
import "./globals.css";
export const metadata={title:"AURAAI — Personal assistant",description:"A calm, personal space for planning, opportunities and reflection."};
export default function RootLayout({children}:{children:React.ReactNode}){return <html lang="en"><body><AppShell>{children}</AppShell></body></html>}
