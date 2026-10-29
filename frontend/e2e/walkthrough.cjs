const { chromium } = require("playwright");
// Browser walkthrough of the main customer and restaurant journeys against a running app.
// Usage: node e2e/walkthrough.cjs <output-dir> [base-url]
// Needs: Playwright with Chromium, demo data (SEED_DEMO_DATA=true) on a fresh database, and a label
// photo at <output-dir>/label.png (backend: python -c "from tests.test_packaged import label_image; ...").
// Screenshots are written to <output-dir>/shots/.
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
  const shot = (name) => page.screenshot({ path: `${S}/shots/${name}.png`, fullPage: true });

  // ---------- customer ----------
  await page.goto(BASE + "/");
  await page.waitForURL(/\/login/);
  await page.fill('input[type="email"]', "customer@demo.allergyguard.app");
  await page.fill('input[type="password"]', "Demo1234!");
  await page.click('button:has-text("Sign in")');
  await page.waitForSelector("text=What would you like to check?");
  log("home chips:", await page.locator(".card .chip").allInnerTexts());
  await shot("01-home");

  // Typed ingredient list
  await page.click("text=Type or paste it instead");
  await page.fill("textarea", "Ingredients: rice, sugar, sunflower oil, salt");
  await page.fill('input[placeholder="May contain nuts."]', "May contain peanuts.");
  await page.click('button:has-text("Check ingredients")');
  await page.waitForSelector(".verdict");
  log("text check verdict:", (await page.locator(".verdict h2").innerText()));
  log("  cards:", await page.locator(".allergen-card .badge").allInnerTexts());
  await shot("02-text-result");

  // Packaged photo
  await page.click('nav >> text=Packaged food');
  await page.setInputFiles('.photo-add input', `${S}/label.png`);
  await page.waitForSelector(".photo img");
  await page.click('button:has-text("Check this product")');
  await page.waitForSelector(".verdict", { timeout: 60000 });
  log("packaged verdict:", await page.locator(".verdict h2").innerText());
  log("  cards:", await page.locator(".allergen-card").evaluateAll((els) => els.map((e) => e.querySelector("strong").innerText + " = " + e.querySelector(".badge").innerText)));
  log("  limitations:", await page.locator("section.card:has(h3:text('Limitations')) li").allInnerTexts());
  await shot("03-packaged-result");

  // Restaurant: registered, choose dish from menu
  await page.click('nav >> text=Restaurant food');
  await page.fill('input[type="search"]', "spice");
  await page.click('li:has-text("Spice Route Kitchen") button:has-text("Select")');
  await page.waitForSelector('select:has(option:text("Not sure / not listed"))');
  await page.selectOption('select:has(option:text("Not sure / not listed"))', { label: "Main: Butter Chicken" });
  await page.click('button:has-text("Check this dish")');
  await page.waitForSelector(".verdict");
  log("butter chicken verdict:", await page.locator(".verdict h2").innerText());
  log("  cards:", await page.locator(".allergen-card").evaluateAll((els) => els.map((e) => e.querySelector("strong").innerText + " = " + e.querySelector(".badge").innerText)));
  log("  ask staff:", (await page.locator(".checklist li").allInnerTexts()).length, "questions");
  await shot("04-restaurant-result");

  // Restaurant: typed dish name -> Chana Masala -> suggest correction
  await page.selectOption('select:has(option:text("Not sure / not listed"))', { label: "Not sure / not listed" });
  await page.fill('input[placeholder="e.g. Butter chicken"]', "chana masla");
  await page.click('button:has-text("Check this dish")');
  await page.waitForSelector(".verdict");
  log("chana masala verdict:", await page.locator(".verdict h2").innerText(), "| dish card:", await page.locator(".card .badge.ok").first().innerText());
  await page.click('button:has-text("Suggest a correction")');
  await page.fill('label:has-text("Ingredient") input', "ghee");
  await page.fill('label:has-text("Details") textarea', "Tasted of ghee");
  await page.click('button:has-text("Send suggestion")');
  await page.waitForSelector("text=stays marked as unverified");
  await shot("05-feedback-sent");

  // Unregistered restaurant
  await page.click('button[role="tab"]:has-text("Restaurant not listed")');
  await page.fill('label:has-text("Restaurant name") input', "Corner Cafe");
  await page.fill('input[placeholder="e.g. Butter chicken"]', "pad thai");
  await page.click('button:has-text("Check this dish")');
  await page.waitForSelector(".verdict");
  log("unregistered verdict:", await page.locator(".verdict h2").innerText());
  await shot("06-unregistered");

  await page.click('nav >> text=My suggestions');
  await page.waitForSelector("text=Awaiting review");
  log("my suggestions:", await page.locator(".card strong").allInnerTexts());

  // Profile edit
  await page.click('nav >> text=My allergies');
  await page.check('label.check:has-text("Sesame") input');
  await page.click('button:has-text("Save allergies")');
  await page.waitForSelector("text=Saved.");
  await page.click('nav >> text=Home');
  await page.waitForSelector(".card .chip:has-text('Sesame')");
  log("home chips after edit:", await page.locator(".card .chip").allInnerTexts());
  await page.click('button:has-text("Sign out")');
  await page.waitForURL(/\/login/);

  // ---------- restaurant owner ----------
  await page.fill('input[type="email"]', "owner@demo.allergyguard.app");
  await page.fill('input[type="password"]', "Demo1234!");
  await page.click('button:has-text("Sign in")');
  await page.waitForSelector(".stat");
  log("owner stats:", await page.locator(".stat").allInnerTexts());
  await shot("07-owner-overview");

  await page.click('.sidebar >> text=Feedback');
  await page.waitForSelector('button.primary:has-text("Confirm")');
  await shot("08-feedback-review");
  await page.fill('input[aria-label="Decision note"]', "Chef confirmed ghee in the tadka");
  await page.click('button.primary:has-text("Confirm")');
  await page.waitForSelector("text=Nothing here.");
  await page.click('button[role="tab"]:has-text("Confirmed")');
  await page.waitForSelector(".card small.muted");
  log("confirmed:", await page.locator(".card small.muted").last().innerText());

  await page.click('.sidebar >> text=Menu');
  await page.waitForSelector("table");
  log("menu rows:", await page.locator("tbody tr").evaluateAll((rows) => rows.map((r) => r.innerText.replace(/\s+/g, " "))));
  await page.click('tbody tr:has-text("Chana Masala")');
  await page.waitForSelector("text=Current recipe");
  log("chana recipe heading:", await page.locator("h2:has-text('Current recipe')").innerText());
  await shot("09-menu-item");

  // Add a dish with the recipe editor
  await page.click('.sidebar >> text=Menu');
  await page.click('button:has-text("+ Add dish")');
  await page.fill('label:has-text("Dish name") input', "Paneer Tikka");
  await page.fill('label:has-text("Category") input', "Starter");
  await page.fill('input[aria-label="Ingredient 1 name"]', "paneer");
  await page.click('button:has-text("+ Add ingredient")');
  await page.fill('input[aria-label="Ingredient 2 name"]', "yogurt marinade");
  await page.click('button:has-text("+ Add ingredient")');
  await page.fill('input[aria-label="Ingredient 3 name"]', "mustard oil");
  await page.click('button:has-text("Suggest allergens from names")');
  await page.waitForSelector("text=suggested allergen");
  log("suggest msg:", await page.locator("small:has-text('suggested allergen')").innerText());
  await page.click('button:has-text("+ Add cross-contact")');
  await page.selectOption('select[aria-label="Cross-contact allergen"]', "peanuts");
  await page.fill('input[aria-label="Cross-contact description"]', "Shared tandoor skewers");
  await shot("10-new-dish-form");
  await page.click('button:has-text("Save dish")');
  await page.waitForSelector("text=Current recipe");
  log("new dish:", await page.locator("h1").innerText(), "|", await page.locator(".card .badge").first().innerText());
  log("  contains:", await page.locator("td .badge").allInnerTexts());
  await page.click('button:has-text("Mark verified")');
  await page.waitForSelector('.card .badge.ok:has-text("verified")');
  await shot("11-new-dish-verified");

  await page.click('.sidebar >> text=Audit log');
  await page.waitForSelector("table");
  log("audit actions:", (await page.locator("td .mono").allInnerTexts()).slice(0, 5));
  await page.click('.sidebar >> text=Team');
  await page.waitForSelector("table");
  await page.click('button:has-text("Sign out")');

  // ---------- customer re-check after confirmation ----------
  await page.fill('input[type="email"]', "customer@demo.allergyguard.app");
  await page.fill('input[type="password"]', "Demo1234!");
  await page.click('button:has-text("Sign in")');
  await page.click('nav >> text=Restaurant food');
  await page.click('li:has-text("Spice Route Kitchen") button:has-text("Select")');
  await page.selectOption('select:has(option:text("Not sure / not listed"))', { label: "Main: Chana Masala" });
  await page.click('button:has-text("Check this dish")');
  await page.waitForSelector(".verdict");
  log("chana after confirm:", await page.locator(".allergen-card").evaluateAll((els) => els.map((e) => e.querySelector("strong").innerText + " = " + e.querySelector(".badge").innerText)));
  await page.click('nav >> text=My suggestions');
  await page.waitForSelector(".card .badge");
  log("my suggestion status:", await page.locator(".card .badge").first().innerText());

  // ---------- new customer sign-up, mobile ----------
  const m = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
  const mp = await m.newPage();
  mp.on("pageerror", (e) => errors.push(String(e)));
  await mp.goto(BASE + "/signup");
  await mp.fill('label:has-text("Your name") input', "Mobile Tester");
  await mp.fill('label:has-text("Email") input', "mobile@example.com");
  await mp.fill('label:has-text("Password") input', "Passw0rd!");
  await mp.click('button:has-text("Create account")');
  log("signup without allergies:", await mp.locator(".alert.error").innerText());
  await mp.check('label.check:has-text("Eggs") input');
  await mp.fill('input[aria-label="Add another allergy"]', "Kiwi");
  await mp.click('button:has-text("Add")');
  await mp.screenshot({ path: `${S}/shots/12-mobile-signup.png`, fullPage: true });
  await mp.click('button:has-text("Create account")');
  await mp.waitForSelector("text=What would you like to check?");
  log("mobile home chips:", await mp.locator(".card .chip").allInnerTexts());
  await mp.goto(BASE + "/check/text");
  await mp.fill("textarea", "flour, egg, kiwi, sugar");
  await mp.click('button:has-text("Check ingredients")');
  await mp.waitForSelector(".verdict");
  log("mobile result:", await mp.locator(".allergen-card").evaluateAll((els) => els.map((e) => e.querySelector("strong").innerText.trim() + " = " + e.querySelector(".badge").innerText)));
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  log("mobile horizontal overflow:", overflow);
  await mp.screenshot({ path: `${S}/shots/13-mobile-result.png`, fullPage: true });
  // Customer can't open dashboard
  await mp.goto(BASE + "/dashboard");
  log("customer visiting /dashboard ends at:", new URL(mp.url()).pathname);

  log("\nbrowser errors:", errors.length ? errors : "none");
  await browser.close();
})().catch((e) => {
  console.error("E2E FAILED:", e.message);
  process.exit(1);
});
