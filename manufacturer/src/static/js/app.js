/**
 * 3D Printer Production Simulator - Vanilla JS Frontend Client
 * Handles real-time API interactions and modern dynamic DOM updates.
 */

const API_BASE = ''; // Same origin

// ── DOM References ────────────────────────────────────────────────────────
const els = {
    dayCounter: document.getElementById('current-day'),
    btnAdvance: document.getElementById('btn-advance'),
    ordersBody: document.getElementById('orders-body'),
    invContainer: document.getElementById('inventory-container'),
    capUsed: document.getElementById('capacity-used'),
    capTotal: document.getElementById('capacity-total'),
    capFill: document.getElementById('capacity-fill'),
    supplierSelect: document.getElementById('supplier-select'),
    poList: document.getElementById('po-list'),
    poForm: document.getElementById('purchase-form'),
    poQty: document.getElementById('purchase-qty'),
    poInfo: document.getElementById('purchase-info'),
    estCost: document.getElementById('est-cost'),
    leadTime: document.getElementById('lead-time'),
    eventLog: document.getElementById('event-log'),
    toastCon: document.getElementById('toast-container')
};

// ── State ───────────────────────────────────────────────────────────────
let state = {
    suppliers: []
};

// ── Initialization ──────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', async () => {
    await fetchAllData();
    setupEventListeners();
});

function setupEventListeners() {
    els.btnAdvance.addEventListener('click', advanceSimulation);
    
    // Purchase form
    els.poForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        await issuePurchaseOrder();
    });

    // PO Form calculation update
    els.supplierSelect.addEventListener('change', updatePurchaseInfo);
    els.poQty.addEventListener('input', updatePurchaseInfo);
}

// ── Core Fetchers ──────────────────────────────────────────────────────
async function fetchAllData() {
    els.btnAdvance.disabled = true;
    try {
        await Promise.all([
            fetchSimState(),
            fetchInventory(),
            fetchOrders(),
            fetchSuppliers(),
            fetchEvents()
        ]);
        updatePurchaseInfo();
    } catch (e) {
        showToast('Error connecting to the API', 'error');
    } finally {
        els.btnAdvance.disabled = false;
    }
}

async function fetchSimState() {
    const res = await fetch('/simulation/state');
    const data = await res.json();
    els.dayCounter.textContent = data.current_day;
    
    els.capUsed.textContent = data.in_progress_count;
    els.capTotal.textContent = data.capacity_per_day;
    
    const percentage = Math.min(100, (data.in_progress_count / data.capacity_per_day) * 100);
    els.capFill.style.width = `${percentage}%`;
    if(percentage > 80) els.capFill.style.background = 'var(--color-warning)';
    else els.capFill.style.background = 'var(--accent-primary)';
}

async function fetchInventory() {
    const res = await fetch('/inventory');
    const inv = await res.json();
    
    els.invContainer.innerHTML = '';
    inv.forEach(item => {
        const stockStatus = item.available <= 5 ? 'critical' : (item.available <= 15 ? 'low' : 'normal');
        
        els.invContainer.innerHTML += `
            <div class="inv-item ${stockStatus}">
                <span class="item-name">${item.product_name}</span>
                <div class="item-stats">
                    <span>Avail: <strong>${item.available}</strong></span>
                    <span>Res: ${item.reserved}</span>
                </div>
            </div>
        `;
    });
}

async function fetchOrders() {
    const res = await fetch('/api/orders');
    const orders = await res.json();

    els.ordersBody.innerHTML = '';
    if (orders.length === 0) {
        els.ordersBody.innerHTML = '<tr><td colspan="5" style="text-align:center;opacity:0.5">No sales orders yet</td></tr>';
        return;
    }
    orders.forEach(o => {
        let actHtml = '-';
        if (o.status === 'pending') {
            actHtml = `<button class="btn btn-secondary btn-sm" onclick="releaseOrder(${o.id})">Release</button>`;
        }

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>#SO-${o.id}</td>
            <td><strong>${o.model}</strong><br><small style="opacity:0.6">${o.retailer_name}</small></td>
            <td>x${o.quantity}</td>
            <td><span class="status-badge status-${o.status}">${o.status}</span></td>
            <td>${actHtml}</td>
        `;
        els.ordersBody.appendChild(tr);
    });
}

async function fetchSuppliers() {
    const res = await fetch('/suppliers');
    state.suppliers = await res.json();
    
    // Fill Select
    els.supplierSelect.innerHTML = '';
    state.suppliers.forEach(s => {
        const opt = document.createElement('option');
        opt.value = s.id;
        opt.textContent = `${s.name} (${s.product_id})`; // Simplify since we dont send product name explicitly in supplier model
        els.supplierSelect.appendChild(opt);
    });
    
    // Fetch POs
    fetchPurchaseOrders();
}

async function fetchPurchaseOrders() {
    const res = await fetch('/purchase-orders?status=pending');
    const pos = await res.json();
    
    els.poList.innerHTML = '';
    pos.forEach(po => {
        els.poList.innerHTML += `
            <li>
                <span><strong>PO-${po.id}</strong>: ${po.supplier_name} (x${po.quantity})</span>
                <span style="color:var(--accent-primary)">Day ${po.expected_delivery}</span>
            </li>
        `;
    });
}

async function fetchEvents() {
    const res = await fetch('/simulation/events?limit=10');
    const events = await res.json();
    
    els.eventLog.innerHTML = '';
    events.forEach(ev => {
        const details = ev.details ? JSON.parse(ev.details) : {};
        let label = ev.event_type.replace(/_/g, ' ');
        const urgent = ev.event_type.includes('ERROR') || ev.event_type.includes('SHORTAGE') ? 'urgent' : '';
        
        els.eventLog.innerHTML += `
            <div class="event-log-entry ${urgent}">
                <strong>[D-${ev.sim_date}]</strong> ${label} 
                <span class="topic">${ev.category ? '(' + ev.category + ')' : ''}</span>
            </div>
        `;
    });
}

// ── Interactivity ──────────────────────────────────────────────────────

function updatePurchaseInfo() {
    const supId = parseInt(els.supplierSelect.value);
    const qty = parseInt(els.poQty.value) || 0;
    
    const supplier = state.suppliers.find(s => s.id === supId);
    if (supplier) {
        els.poInfo.classList.remove('hidden');
        els.estCost.textContent = (supplier.unit_cost * qty).toFixed(2);
        els.leadTime.textContent = supplier.lead_time_days;
        
        // Validation minimum
        if (qty < supplier.min_order_qty) {
            els.poQty.setCustomValidity(`Minimum order is ${supplier.min_order_qty}`);
            els.poQty.reportValidity();
        } else {
            els.poQty.setCustomValidity("");
        }
    }
}

async function issuePurchaseOrder() {
    const supId = parseInt(els.supplierSelect.value);
    const qty = parseInt(els.poQty.value);
    
    const res = await fetch('/purchase-orders', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ supplier_id: supId, quantity: qty })
    });
    
    if (res.ok) {
        showToast('Purchase Order Issued!', 'success');
        els.poQty.value = 10;
        await fetchPurchaseOrders();
        await fetchEvents();
    } else {
        const error = await res.json();
        showToast(error.detail, 'error');
    }
}

async function releaseOrder(orderId) {
    const res = await fetch(`/api/orders/${orderId}/release`, { method: 'POST' });
    
    if (res.ok) {
        showToast(`Order MO-${orderId} Released for production`, 'success');
        await fetchOrders();
        await fetchEvents();
    } else {
        const error = await res.json();
        // If specific shortage breakdown
        if (error.detail && error.detail.title) {
            showToast(`Shortage: Missing ${error.detail.shortages[0].shortage}x ${error.detail.shortages[0].material}`, 'error');
        } else {
            showToast(error.detail || 'Failed to release order', 'error');
        }
    }
}

async function advanceSimulation() {
    els.btnAdvance.disabled = true;
    els.btnAdvance.textContent = 'Simulating...';
    
    try {
        const res = await fetch('/simulation/advance', { method: 'POST' });
        if (res.ok) {
            // Flash screen subtlely
            document.body.style.opacity = '0.9';
            setTimeout(() => document.body.style.opacity = '1', 100);
            
            showToast('Day advanced successfully');
            await fetchAllData();
        } else {
            showToast('Simulation failed to advance', 'error');
        }
    } catch(e) {
        showToast('Connection error', 'error');
    } finally {
        els.btnAdvance.disabled = false;
        els.btnAdvance.textContent = 'Advance Day';
    }
}

// ── UI Utils ────────────────────────────────────────────────────────────
function showToast(message, type = 'success') {
    const t = document.createElement('div');
    t.className = `toast ${type}`;
    t.textContent = message;
    els.toastCon.appendChild(t);
    
    setTimeout(() => {
        t.style.opacity = '0';
        t.style.transform = 'translateY(10px)';
        setTimeout(() => t.remove(), 300);
    }, 4000);
}
