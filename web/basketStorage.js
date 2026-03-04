/**
 * BasketStorage — LocalStorage tabanlı sepet sistemi (CRUD + validasyon)
 * localStorage anahtarı: baskets_v1
 * Maks 5 sepet, her sepet 0-1.000.000 TL bakiye
 * window.BasketStorage namespace'ine atanır.
 */
(function () {
  'use strict';

  const BASKETS_STORAGE_KEY = 'baskets_v1';
  const MAX_BASKETS = 5;
  const MAX_BALANCE = 1_000_000;
  const MAX_NAME_LENGTH = 40;

  // ── Helpers ──────────────────────────────────────────────

  function generateId() {
    if (typeof crypto !== 'undefined' && crypto.randomUUID) {
      return crypto.randomUUID();
    }
    // Fallback UUID v4
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  function now() {
    return new Date().toISOString();
  }

  function loadBaskets() {
    try {
      var raw = localStorage.getItem(BASKETS_STORAGE_KEY);
      if (!raw) return [];
      var parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch (e) {
      return [];
    }
  }

  function saveBaskets(baskets) {
    localStorage.setItem(BASKETS_STORAGE_KEY, JSON.stringify(baskets));
  }

  // ── Validation ───────────────────────────────────────────

  function validateName(name, baskets, excludeId) {
    if (typeof name !== 'string' || name.trim().length === 0) {
      throw new Error('Sepet adı boş olamaz.');
    }
    if (name.trim().length > MAX_NAME_LENGTH) {
      throw new Error('Sepet adı en fazla ' + MAX_NAME_LENGTH + ' karakter olabilir.');
    }
    var lower = name.trim().toLowerCase();
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id !== excludeId && baskets[i].name.trim().toLowerCase() === lower) {
        throw new Error('Bu isimde bir sepet zaten mevcut: "' + name.trim() + '"');
      }
    }
  }

  function validateBalance(balance) {
    if (typeof balance !== 'number' || !Number.isFinite(balance)) {
      throw new Error('Bakiye geçerli bir sayı olmalıdır.');
    }
    if (balance < 0) {
      throw new Error('Bakiye 0\'dan küçük olamaz.');
    }
    if (balance > MAX_BALANCE) {
      throw new Error('Bakiye en fazla ' + MAX_BALANCE.toLocaleString('tr-TR') + ' olabilir.');
    }
  }

  function validateSymbol(symbol) {
    if (typeof symbol !== 'string' || symbol.trim().length === 0) {
      throw new Error('Sembol boş olamaz.');
    }
    if (!symbol.trim().endsWith('.IS')) {
      throw new Error('Sembol ".IS" uzantısıyla bitmelidir (örn: THYAO.IS).');
    }
  }

  function validateTransactionInput(tx) {
    if (!tx || typeof tx !== 'object') {
      throw new Error('İşlem verisi geçersiz.');
    }
    if (tx.type !== 'BUY' && tx.type !== 'SELL') {
      throw new Error('İşlem tipi "BUY" veya "SELL" olmalıdır.');
    }
    validateSymbol(tx.symbol);
    if (!Number.isInteger(tx.quantity) || tx.quantity < 1) {
      throw new Error('Adet pozitif bir tam sayı olmalıdır.');
    }
    if (typeof tx.pricePerUnit !== 'number' || !Number.isFinite(tx.pricePerUnit) || tx.pricePerUnit <= 0) {
      throw new Error('Birim fiyat pozitif bir sayı olmalıdır.');
    }
  }

  // ── Performance Calculation Helpers ──────────────────────

  function buildHoldingsFromTransactions(transactions) {
    var holdingMap = {};

    for (var i = 0; i < transactions.length; i++) {
      var tx = transactions[i];
      if (!holdingMap[tx.symbol]) {
        holdingMap[tx.symbol] = { quantity: 0, totalCost: 0 };
      }
      var h = holdingMap[tx.symbol];
      if (tx.type === 'BUY') {
        h.totalCost += tx.totalAmount;
        h.quantity += tx.quantity;
      } else if (tx.type === 'SELL') {
        var avgCostBefore = h.quantity > 0 ? h.totalCost / h.quantity : 0;
        h.quantity -= tx.quantity;
        h.totalCost = h.quantity > 0 ? avgCostBefore * h.quantity : 0;
      }
    }

    return holdingMap;
  }

  function computeRealizedPnL(transactions) {
    var holdingMap = {};
    var realized = 0;

    for (var i = 0; i < transactions.length; i++) {
      var tx = transactions[i];
      if (!holdingMap[tx.symbol]) {
        holdingMap[tx.symbol] = { quantity: 0, totalCost: 0 };
      }
      var h = holdingMap[tx.symbol];
      if (tx.type === 'BUY') {
        h.totalCost += tx.totalAmount;
        h.quantity += tx.quantity;
      } else if (tx.type === 'SELL') {
        var avgCost = h.quantity > 0 ? h.totalCost / h.quantity : 0;
        realized += (tx.pricePerUnit - avgCost) * tx.quantity;
        h.quantity -= tx.quantity;
        h.totalCost = h.quantity > 0 ? avgCost * h.quantity : 0;
      }
    }

    return realized;
  }

  function emptyPerformance() {
    return {
      totalInvested: 0,
      totalReturned: 0,
      realizedPnL: 0,
      unrealizedPnL: 0,
      totalPnL: 0,
      returnPct: 0,
      holdings: [],
      lastCalculatedAt: now()
    };
  }

  // ── CRUD Functions ───────────────────────────────────────

  /**
   * createBasket(name, initialBalance) → Basket
   * Yeni sepet oluşturur. 5 sepet sınırına takılırsa hata fırlatır.
   */
  function createBasket(name, initialBalance) {
    var baskets = loadBaskets();

    if (baskets.length >= MAX_BASKETS) {
      throw new Error('Maksimum ' + MAX_BASKETS + ' sepet oluşturulabilir.');
    }

    validateName(name, baskets, null);
    validateBalance(initialBalance);

    var basket = {
      id: generateId(),
      name: name.trim(),
      initialBalance: initialBalance,
      currentBalance: initialBalance,
      createdAt: now(),
      lastResetAt: null,
      transactions: [],
      performance: emptyPerformance()
    };

    baskets.push(basket);
    saveBaskets(baskets);
    return basket;
  }

  /**
   * listBaskets() → Basket[]
   * baskets_v1 anahtarından tüm sepetleri döner.
   */
  function listBaskets() {
    return loadBaskets();
  }

  /**
   * getBasketById(id) → Basket | null
   * Tekil sepet getirir.
   */
  function getBasketById(id) {
    var baskets = loadBaskets();
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === id) return baskets[i];
    }
    return null;
  }

  /**
   * updateBasket(id, updates) → Basket
   * Sepet adı veya başlangıç bakiyesini günceller.
   * İşlem varsa bakiye değiştirilemez.
   */
  function updateBasket(id, updates) {
    var baskets = loadBaskets();
    var index = -1;
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === id) { index = i; break; }
    }
    if (index === -1) {
      throw new Error('Sepet bulunamadı: ' + id);
    }

    var basket = baskets[index];

    if (updates && typeof updates.name === 'string') {
      validateName(updates.name, baskets, id);
      basket.name = updates.name.trim();
    }

    if (updates && typeof updates.initialBalance === 'number') {
      if (basket.transactions.length > 0) {
        throw new Error('İşlem bulunan sepetlerde başlangıç bakiyesi değiştirilemez.');
      }
      validateBalance(updates.initialBalance);
      basket.initialBalance = updates.initialBalance;
      basket.currentBalance = updates.initialBalance;
    }

    baskets[index] = basket;
    saveBaskets(baskets);
    return basket;
  }

  /**
   * deleteBasket(id) → boolean
   * Sepeti siler. Başarılı ise true.
   */
  function deleteBasket(id) {
    var baskets = loadBaskets();
    var newBaskets = [];
    var found = false;
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === id) {
        found = true;
      } else {
        newBaskets.push(baskets[i]);
      }
    }
    if (!found) return false;
    saveBaskets(newBaskets);
    return true;
  }

  /**
   * resetBasket(id) → Basket
   * Tüm transactions'ı temizler, currentBalance'ı initialBalance'a eşitler,
   * performance sıfırlar, lastResetAt'ı şu anki zamana set eder.
   */
  function resetBasket(id) {
    var baskets = loadBaskets();
    var index = -1;
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === id) { index = i; break; }
    }
    if (index === -1) {
      throw new Error('Sepet bulunamadı: ' + id);
    }

    var basket = baskets[index];
    basket.transactions = [];
    basket.currentBalance = basket.initialBalance;
    basket.lastResetAt = now();
    basket.performance = emptyPerformance();

    baskets[index] = basket;
    saveBaskets(baskets);
    return basket;
  }

  /**
   * addTransaction(basketId, tx) → Transaction
   * Sepete alım/satım işlemi ekler.
   * BUY'da bakiye kontrolü, SELL'de hisse yeterliliği kontrolü yapar.
   */
  function addTransaction(basketId, tx) {
    validateTransactionInput(tx);

    var baskets = loadBaskets();
    var index = -1;
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === basketId) { index = i; break; }
    }
    if (index === -1) {
      throw new Error('Sepet bulunamadı: ' + basketId);
    }

    var basket = baskets[index];
    var totalAmount = tx.quantity * tx.pricePerUnit;

    if (tx.type === 'BUY') {
      if (totalAmount > basket.currentBalance) {
        throw new Error(
          'Yetersiz bakiye. Gerekli: ' + totalAmount.toFixed(2) +
          ', Mevcut: ' + basket.currentBalance.toFixed(2)
        );
      }
      basket.currentBalance -= totalAmount;
    } else if (tx.type === 'SELL') {
      // Elde yeterli hisse var mı kontrolü
      var holdingMap = buildHoldingsFromTransactions(basket.transactions);
      var held = holdingMap[tx.symbol] ? holdingMap[tx.symbol].quantity : 0;
      if (held < tx.quantity) {
        throw new Error(
          'Yetersiz hisse. Elde: ' + held + ' adet ' + tx.symbol +
          ', Satılmak istenen: ' + tx.quantity
        );
      }
      basket.currentBalance += totalAmount;
    }

    var transaction = {
      id: generateId(),
      type: tx.type,
      symbol: tx.symbol.trim(),
      quantity: tx.quantity,
      pricePerUnit: tx.pricePerUnit,
      totalAmount: totalAmount,
      date: now()
    };

    basket.transactions.push(transaction);
    baskets[index] = basket;
    saveBaskets(baskets);
    return transaction;
  }

  /**
   * removeTransaction(basketId, txId) → boolean
   * İşlemi siler ve bakiyeyi/holdingleri yeniden hesaplar.
   */
  function removeTransaction(basketId, txId) {
    var baskets = loadBaskets();
    var index = -1;
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === basketId) { index = i; break; }
    }
    if (index === -1) return false;

    var basket = baskets[index];
    var txIndex = -1;
    for (var j = 0; j < basket.transactions.length; j++) {
      if (basket.transactions[j].id === txId) { txIndex = j; break; }
    }
    if (txIndex === -1) return false;

    // İşlemi sil
    basket.transactions.splice(txIndex, 1);

    // Bakiyeyi sıfırdan hesapla
    basket.currentBalance = basket.initialBalance;
    for (var k = 0; k < basket.transactions.length; k++) {
      var t = basket.transactions[k];
      if (t.type === 'BUY') {
        basket.currentBalance -= t.totalAmount;
      } else if (t.type === 'SELL') {
        basket.currentBalance += t.totalAmount;
      }
    }

    baskets[index] = basket;
    saveBaskets(baskets);
    return true;
  }

  /**
   * recalcPerformance(basketId, livePrices) → Performance
   * Güncel fiyatlarla performance nesnesini yeniden hesaplar.
   * livePrices: { "THYAO.IS": 320.50, "ASELS.IS": 295.20, ... }
   */
  function recalcPerformance(basketId, livePrices) {
    var baskets = loadBaskets();
    var index = -1;
    for (var i = 0; i < baskets.length; i++) {
      if (baskets[i].id === basketId) { index = i; break; }
    }
    if (index === -1) {
      throw new Error('Sepet bulunamadı: ' + basketId);
    }

    var basket = baskets[index];
    var transactions = basket.transactions;
    var prices = livePrices || {};

    // totalInvested / totalReturned
    var totalInvested = 0;
    var totalReturned = 0;
    for (var j = 0; j < transactions.length; j++) {
      if (transactions[j].type === 'BUY') {
        totalInvested += transactions[j].totalAmount;
      } else {
        totalReturned += transactions[j].totalAmount;
      }
    }

    // Holdings
    var holdingMap = buildHoldingsFromTransactions(transactions);
    var holdings = [];
    var unrealizedPnL = 0;

    var symbols = Object.keys(holdingMap);
    for (var s = 0; s < symbols.length; s++) {
      var sym = symbols[s];
      var h = holdingMap[sym];
      if (h.quantity <= 0) continue;

      var avgCost = h.totalCost / h.quantity;
      var currentPrice = typeof prices[sym] === 'number' ? prices[sym] : avgCost;
      var marketValue = h.quantity * currentPrice;
      var pnl = marketValue - (h.quantity * avgCost);
      var pnlPct = (h.quantity * avgCost) > 0 ? (pnl / (h.quantity * avgCost)) * 100 : 0;

      holdings.push({
        symbol: sym,
        quantity: h.quantity,
        avgCost: Math.round(avgCost * 100) / 100,
        currentPrice: currentPrice,
        marketValue: Math.round(marketValue * 100) / 100,
        pnl: Math.round(pnl * 100) / 100,
        pnlPct: Math.round(pnlPct * 100) / 100
      });

      unrealizedPnL += pnl;
    }

    var realizedPnL = computeRealizedPnL(transactions);
    var totalPnL = realizedPnL + unrealizedPnL;
    var returnPct = basket.initialBalance > 0 ? (totalPnL / basket.initialBalance) * 100 : 0;

    var performance = {
      totalInvested: Math.round(totalInvested * 100) / 100,
      totalReturned: Math.round(totalReturned * 100) / 100,
      realizedPnL: Math.round(realizedPnL * 100) / 100,
      unrealizedPnL: Math.round(unrealizedPnL * 100) / 100,
      totalPnL: Math.round(totalPnL * 100) / 100,
      returnPct: Math.round(returnPct * 100) / 100,
      holdings: holdings,
      lastCalculatedAt: now()
    };

    basket.performance = performance;
    baskets[index] = basket;
    saveBaskets(baskets);
    return performance;
  }

  /**
   * persistBaskets(baskets) → void
   * Tüm diziyi baskets_v1 anahtarıyla localStorage'a yazar.
   */
  function persistBaskets(baskets) {
    if (!Array.isArray(baskets)) {
      throw new Error('Baskets bir dizi olmalıdır.');
    }
    if (baskets.length > MAX_BASKETS) {
      throw new Error('Maksimum ' + MAX_BASKETS + ' sepet saklanabilir.');
    }
    saveBaskets(baskets);
  }

  // ── Public API ───────────────────────────────────────────

  window.BasketStorage = {
    createBasket: createBasket,
    listBaskets: listBaskets,
    getBasketById: getBasketById,
    updateBasket: updateBasket,
    deleteBasket: deleteBasket,
    resetBasket: resetBasket,
    addTransaction: addTransaction,
    removeTransaction: removeTransaction,
    recalcPerformance: recalcPerformance,
    persistBaskets: persistBaskets,
    // Exposed constants for external use
    MAX_BASKETS: MAX_BASKETS,
    MAX_BALANCE: MAX_BALANCE,
    STORAGE_KEY: BASKETS_STORAGE_KEY
  };

})();
