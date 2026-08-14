// Render a local HTML file to PDF using the Electron already in devDependencies.
//
// There is no browser to press Ctrl+P in from a script, and the page's charts
// are drawn by JavaScript at load time -- so a static HTML-to-PDF converter
// would produce a document with empty figures. Electron runs the real renderer,
// which means the SVG the reader sees is the SVG that gets printed.
//
//   npx electron tools/html_to_pdf.js <input.html> <output.pdf>
//
// The page's own @media print block does the styling (light palette, no page
// breaks inside figures, charts fit rather than scroll), so nothing about the
// layout is decided here.
const { app, BrowserWindow } = require("electron");
const path = require("path");
const fs = require("fs");

const input = process.argv[2];
const output = process.argv[3];

if (!input || !output) {
  console.error("usage: npx electron tools/html_to_pdf.js <input.html> <output.pdf>");
  process.exit(1);
}
const src = path.resolve(input);
if (!fs.existsSync(src)) {
  console.error(`not found: ${src}`);
  process.exit(1);
}

app.on("ready", async () => {
  const win = new BrowserWindow({
    show: false,
    width: 1200,
    height: 1600,
    webPreferences: { offscreen: true },
  });

  await win.loadFile(src);

  // The charts render synchronously on script execution, but fonts and layout
  // settle a frame or two later. Waiting on a fixed delay is crude; waiting for
  // the chart nodes to actually exist is not.
  await win.webContents.executeJavaScript(`
    new Promise((resolve) => {
      const ready = () => document.querySelectorAll(".chart svg").length >= 7;
      if (ready()) return requestAnimationFrame(() => resolve(true));
      const t = setInterval(() => {
        if (ready()) { clearInterval(t); requestAnimationFrame(() => resolve(true)); }
      }, 100);
      setTimeout(() => { clearInterval(t); resolve(false); }, 15000);
    })
  `);

  // A collapsed <details> is hidden by the element itself, not by CSS, so the
  // table views would be missing from the PDF. The page opens them on
  // beforeprint, but printToPDF does not fire that event -- do it explicitly.
  await win.webContents.executeJavaScript(`
    document.querySelectorAll("details").forEach(d => d.setAttribute("open", ""));
    document.documentElement.setAttribute("data-theme", "light");
    true
  `);

  const data = await win.webContents.printToPDF({
    pageSize: "A4",
    printBackground: true,
    margins: { top: 0.5, bottom: 0.5, left: 0.5, right: 0.5 },
    preferCSSPageSize: false,
  });

  fs.writeFileSync(path.resolve(output), data);
  console.log(`wrote ${output}  (${(data.length / 2 ** 20).toFixed(2)} MB)`);
  app.quit();
});
