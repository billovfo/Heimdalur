/* Sorterbare tabelhoveder — progressiv forbedring, ingen afhængigheder.
   Uden JS er tabellen en almindelig <table> i kildens egen rækkefølge.
   En celle kan angive sin egen sorteringsnøgle med data-sort (tal først,
   ellers dansk tekstsortering). */
for (const table of document.querySelectorAll("table.ledger")) {
  const body = table.tBodies[0];
  const heads = [...table.querySelectorAll("thead th")];
  if (!body || body.rows.length < 2 || !heads.length) continue;

  heads.forEach((th, col) => {
    if (th.dataset.serverSort !== undefined) return;
    const button = document.createElement("button");
    button.type = "button";
    button.append(th.textContent.trim());
    button.insertAdjacentHTML("beforeend", ' <span class="arrow" aria-hidden="true">▲</span>');
    th.textContent = "";
    th.append(button);

    button.addEventListener("click", () => {
      const asc = th.getAttribute("aria-sort") !== "ascending";
      for (const other of heads) other.removeAttribute("aria-sort");
      th.setAttribute("aria-sort", asc ? "ascending" : "descending");

      const key = (tr) => {
        const cell = tr.cells[col];
        if (!cell) return "";
        return cell.dataset.sort !== undefined ? cell.dataset.sort : cell.textContent.trim();
      };
      const rows = [...body.rows].sort((a, b) => {
        const x = key(a), y = key(b);
        const nx = parseFloat(x), ny = parseFloat(y);
        const cmp = (x !== "" && y !== "" && !isNaN(nx) && !isNaN(ny))
          ? nx - ny
          : x.localeCompare(y, "da");
        return asc ? cmp : -cmp;
      });
      body.append(...rows);
    });
  });
}
