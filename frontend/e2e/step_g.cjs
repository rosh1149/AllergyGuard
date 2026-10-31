const { chromium } = require("playwright");
// Browser check of the Step C-F screens (provenance, history, data export/delete, feedback
// clarification, structured cross-contact, version changes, review queues, admin monitoring).
// Usage: node e2e/step_g.cjs <output-dir> [base-url]   (fresh database with demo data)
const S = process.argv[2];
const BASE = process.argv[3] ?? "http://localhost:4173";
const log = (...a) => console.log(...a);

(async () => {
  const browser = await chromium.launch();
  const errors = [];
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("dialog", (d) => d.accept());
  const shot = (name) => page.screenshot({ path: `${S}/shots/${name}.png`, fullPage: true });
  const login = async (email) => {
    await page.goto(BASE + "/login");
    await page.fill('input[type="email"]', email);
    await page.fill('input[type="password"]', "Demo1234!");
    await page.click('button:has-text("Sign in")');
  };
  const logout = () => page.click('button:has-text("Sign out")');

  // ---------- customer: result details and history ----------
  await login("customer@demo.allergyguard.app");
  await page.waitForSelector("text=What would you like to check?");
  await page.goto(BASE + "/check/text");
  await page.fill("textarea", "Ingredients: rice, butter, spices, salt");
  await page.click('button:has-text("Check ingredients")');
  await page.waitForSelector(".verdict");
  await page.click('.allergen-card >> nth=0 >> summary:has-text("Why")');
  log("why (milk):", (await page.locator(".allergen-card >> nth=0 >> details").innerText()).replace(/\n+/g, " | "));
  await page.click('summary:has-text("Evidence sources")');
  await page.click('summary:has-text("Processing details")');
  log("trace rows:", await page.locator("details:has(summary:has-text('Processing details')) tbody tr").count());
  log("saved link:", await page.locator(".verdict a:has-text('your history')").count());
  await shot("g01-text-result-details");

  await page.click('nav >> text=Past checks');
  await page.waitForSelector("text=Your past checks");
  log("history rows:", await page.locator("ul.plain li").count());
  await page.click("ul.plain li >> nth=0 >> a");
  await page.waitForSelector(".verdict");
  log("history detail:", await page.locator("h1").innerText());
  await shot("g02-history-detail");

  // restaurant: dish not on the menu -> review item; outdated-recipe suggestion
  await page.click('nav >> text=Restaurant food');
  await page.fill('input[type="search"]', "spice");
  await page.click('li:has-text("Spice Route Kitchen") button:has-text("Select")');
  await page.waitForSelector('select:has(option:text("Not sure / not listed"))');
  await page.waitForSelector('label:has-text("Dish name") input');
  await page.fill('label:has-text("Dish name") input', "zebra pie");
  await page.click('button:has-text("Check this dish")');
  await page.waitForSelector(".verdict");
  await page.fill('label:has-text("Dish name") input', "");
  await page.selectOption('select:has(option:text("Not sure / not listed"))', { label: "Main: Butter Chicken" });
  await page.click('button:has-text("Check this dish")');
  await page.waitForSelector(".verdict");
  await page.click('button:has-text("Suggest a correction")');
  await page.selectOption('label:has-text("What\'s wrong?") select', "outdated_recipe");
  await page.fill('label:has-text("Details") textarea', "Now served with a cashew cream sauce");
  await page.click('button:has-text("Send suggestion")');
  await page.waitForSelector("text=Your suggestion was sent");
  await page.click('nav >> text=My allergies');
  await page.waitForSelector("text=Your data");
  await shot("g03-profile-your-data");
  await logout();

  // ---------- restaurant owner ----------
  await login("owner@demo.allergyguard.app");
  await page.waitForSelector(".sidebar");
  await page.click('.sidebar >> text=Feedback');
  await page.waitForSelector("text=Outdated recipe");
  await page.fill('.card:has-text("Outdated recipe") input[aria-label="Decision note"]', "Is it the cashew cream on the lunch menu?");
  await page.click('.card:has-text("Outdated recipe") button:has-text("Ask customer")');
  await page.waitForSelector('.card:has-text("Outdated recipe")', { state: "detached" });
  await page.click('button[role="tab"]:has-text("Waiting for customer")');
  await page.waitForSelector("text=You asked:");
  await shot("g04-dashboard-feedback-question");

  await page.click('.sidebar >> text=Uncertain matches');
  await page.waitForSelector("text=Dish not on the menu");
  log("restaurant review:", (await page.locator(".card >> nth=0").innerText()).replace(/\n+/g, " | "));
  await shot("g05-dashboard-review");

  await page.click('.sidebar >> text=Menu');
  await page.click('tbody tr:has-text("Butter Chicken")');
  await page.click('button:has-text("Edit recipe")');
  await page.waitForSelector(".cc-row");
  await page.selectOption('.cc-row >> nth=0 >> select[aria-label="Cross-contact cause"]', "shared_equipment");
  await page.click('button:has-text("+ Add ingredient")');
  const n = await page.locator('input[aria-label$="name"][aria-label^="Ingredient"]').count();
  await page.fill(`input[aria-label="Ingredient ${n} name"]`, "cashew cream");
  await page.check('label:has-text("Cross-contact has been reviewed") input');
  await page.check('label:has-text("mark as verified") input');
  await page.fill('label:has-text("What changed?") input', "Lunch version uses cashew cream");
  await page.click('button:has-text("Save new version")');
  await page.waitForSelector("text=Cross-contact reviewed by staff.");
  await page.click('summary:has-text("Version history")');
  log("version diff:", (await page.locator("details:has(summary:has-text('Version history')) li >> nth=0").innerText()).replace(/\n+/g, " | "));
  await shot("g06-dashboard-recipe-version");
  await logout();

  // ---------- customer answers; owner confirms ----------
  await login("customer@demo.allergyguard.app");
  await page.waitForSelector("text=What would you like to check?");
  await page.click('nav >> text=My suggestions');
  await page.waitForSelector("text=The restaurant asks:");
  await page.fill('label:has-text("Your answer") textarea', "Yes, the lunch menu one");
  await page.click('button:has-text("Send answer")');
  await page.waitForSelector("text=You answered:");
  await shot("g07-customer-answered");
  await logout();
  await login("owner@demo.allergyguard.app");
  await page.waitForSelector(".sidebar");
  await page.click('.sidebar >> text=Feedback');
  await page.waitForSelector("text=Customer:");
  await page.click('.card:has-text("Outdated recipe") button.primary:has-text("Confirm")');
  await page.waitForSelector('.card:has-text("Outdated recipe")', { state: "detached" });
  await page.click('button[role="tab"]:has-text("Confirmed")');
  await page.waitForSelector("text=Impact:");
  await page.click('.card:has-text("Outdated recipe") summary:has-text("History")');
  log("impact:", await page.locator('.card:has-text("Outdated recipe") >> text=Impact:').innerText());
  await shot("g08-dashboard-feedback-impact");
  await logout();

  // ---------- admin ----------
  await login("admin@demo.allergyguard.app");
  await page.waitForSelector("text=Monitoring");
  await page.waitForSelector(".stat");
  log("admin stats:", (await page.locator(".stat .n").allInnerTexts()).join(" | "));
  await shot("g09-admin-monitoring");
  for (const [link, name, wait] of [["Review queue", "g10-admin-review", ".card, .muted"], ["Unidentified ingredients", "g11-admin-unresolved", "h1"],
    ["Data sources", "g12-admin-sources", "table"], ["Audit log", "g13-admin-audit", "h1"]]) {
    await page.click(`.sidebar >> text=${link}`);
    await page.waitForSelector(wait);
    await page.waitForTimeout(300);
    await shot(name);
  }
  log("sources:", (await page.locator("tbody tr td:first-child strong").allInnerTexts()).join(", "));
  await logout();

  // ---------- phone width ----------
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const p2 = await phone.newPage();
  p2.on("pageerror", (e) => errors.push(String(e)));
  await p2.goto(BASE + "/login");
  await p2.fill('input[type="email"]', "customer@demo.allergyguard.app");
  await p2.fill('input[type="password"]', "Demo1234!");
  await p2.click('button:has-text("Sign in")');
  await p2.waitForSelector("text=What would you like to check?");
  await p2.goto(BASE + "/history");
  await p2.click("ul.plain li >> nth=0 >> a");
  await p2.waitForSelector(".verdict");
  await p2.click('.allergen-card >> nth=0 >> summary:has-text("Why")');
  await p2.click('summary:has-text("Processing details")');
  const overflow = await p2.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  log("phone horizontal overflow px:", overflow);
  await p2.screenshot({ path: `${S}/shots/g14-phone-result.png`, fullPage: true });

  log("console/page errors:", errors.length ? errors : "none");
  await browser.close();
})().catch((e) => { console.error("FAILED:", e); process.exit(1); });
