/* ==========================================================================
   BOM Data Comparison — Shared JavaScript Module
   Lightweight utility library for toast, modal, API, and UI helpers
   ========================================================================== */

;(function (global) {
  'use strict';

  /* ------------------------------------------------------------------------
     DOM Ready Helper
     ------------------------------------------------------------------------ */
  function ready(fn) {
    if (document.readyState !== 'loading') { fn(); return; }
    document.addEventListener('DOMContentLoaded', fn);
  }

  /* ------------------------------------------------------------------------
     Toast Notification System
     ------------------------------------------------------------------------ */
  const Toast = {
    _container: null,

    _ensureContainer() {
      if (this._container) return this._container;
      this._container = document.createElement('div');
      this._container.className = 'toast-container';
      this._container.setAttribute('aria-live', 'polite');
      this._container.setAttribute('aria-label', '通知消息');
      document.body.appendChild(this._container);
      return this._container;
    },

    _createIcon(type) {
      const icons = {
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>',
        warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><line x1="12" y1="16" x2="12" y2="12"/><line x1="12" y1="8" x2="12.01" y2="8"/></svg>'
      };
      return icons[type] || icons.info;
    },

    show(message, type, duration) {
      type = type || 'info';
      duration = duration || (type === 'error' ? 5000 : 3000);

      const container = this._ensureContainer();
      const toast = document.createElement('div');
      toast.className = 'toast toast--' + type;
      toast.setAttribute('role', 'status');

      toast.innerHTML =
        '<span class="toast__icon" aria-hidden="true">' + this._createIcon(type) + '</span>' +
        '<span class="toast__content">' +
          '<span class="toast__message">' + message + '</span>' +
        '</span>' +
        '<button class="toast__close" aria-label="关闭通知">&times;</button>';

      const closeBtn = toast.querySelector('.toast__close');
      const remove = function () {
        toast.classList.add('toast--exiting');
        setTimeout(function () {
          if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 200);
      };

      closeBtn.addEventListener('click', remove);

      if (duration > 0) {
        setTimeout(remove, duration);
      }

      container.appendChild(toast);
      return { remove: remove };
    },

    success(msg, dur) { return this.show(msg, 'success', dur); },
    error(msg, dur) { return this.show(msg, 'error', dur); },
    warning(msg, dur) { return this.show(msg, 'warning', dur); },
    info(msg, dur) { return this.show(msg, 'info', dur); }
  };

  /* ------------------------------------------------------------------------
     Modal Dialog
     ------------------------------------------------------------------------ */
  const Modal = {
    _overlay: null,

    _createOverlay() {
      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay';
      overlay.setAttribute('role', 'dialog');
      overlay.setAttribute('aria-modal', 'true');
      return overlay;
    },

    confirm(title, message, confirmText, cancelText) {
      confirmText = confirmText || '确认';
      cancelText = cancelText || '取消';
      return new Promise(function (resolve) {
        const overlay = Modal._createOverlay();
        overlay.innerHTML =
          '<div class="modal">' +
            '<div class="modal__header">' +
              '<h3 class="modal__title">' + escapeHtml(title) + '</h3>' +
              '<button class="modal__close" aria-label="关闭">&times;</button>' +
            '</div>' +
            '<div class="modal__body"><p>' + escapeHtml(message).replace(/\n/g, '<br>') + '</p></div>' +
            '<div class="modal__footer">' +
              '<button class="btn btn--secondary modal__cancel">' + cancelText + '</button>' +
              '<button class="btn btn--danger modal__confirm">' + confirmText + '</button>' +
            '</div>' +
          '</div>';

        document.body.appendChild(overlay);

        var closed = false;
        function close(result) {
          if (closed) return;
          closed = true;
          overlay.classList.add('modal-overlay--closing');
          var modalEl = overlay.querySelector('.modal');
          if (modalEl) modalEl.classList.add('modal--closing');
          setTimeout(function () {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            resolve(result);
          }, 200);
        }

        overlay.querySelector('.modal__close').addEventListener('click', function () { close(false); });
        overlay.querySelector('.modal__cancel').addEventListener('click', function () { close(false); });
        overlay.querySelector('.modal__confirm').addEventListener('click', function () { close(true); });
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(false); });
        document.addEventListener('keydown', function handler(e) {
          if (e.key === 'Escape') { close(false); document.removeEventListener('keydown', handler); }
        });

        // Focus trap
        var focusable = overlay.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])');
        if (focusable.length > 0) focusable[focusable.length - 1].focus();
      });
    },

    alert(title, message, buttonText) {
      buttonText = buttonText || '知道了';
      return new Promise(function (resolve) {
        const overlay = Modal._createOverlay();
        overlay.innerHTML =
          '<div class="modal">' +
            '<div class="modal__header">' +
              '<h3 class="modal__title">' + escapeHtml(title) + '</h3>' +
              '<button class="modal__close" aria-label="关闭">&times;</button>' +
            '</div>' +
            '<div class="modal__body"><p>' + escapeHtml(message) + '</p></div>' +
            '<div class="modal__footer">' +
              '<button class="btn btn--primary modal__ok">' + buttonText + '</button>' +
            '</div>' +
          '</div>';

        document.body.appendChild(overlay);

        function close() {
          overlay.classList.add('modal-overlay--closing');
          setTimeout(function () {
            if (overlay.parentNode) overlay.parentNode.removeChild(overlay);
            resolve();
          }, 200);
        }

        overlay.querySelector('.modal__close').addEventListener('click', close);
        overlay.querySelector('.modal__ok').addEventListener('click', close);
        overlay.addEventListener('click', function (e) { if (e.target === overlay) close(); });
        document.addEventListener('keydown', function handler(e) {
          if (e.key === 'Escape') { close(); document.removeEventListener('keydown', handler); }
        });
      });
    }
  };

  /* ------------------------------------------------------------------------
     API Helper
     ------------------------------------------------------------------------ */
  const API = {
    _base: '',

    init(baseURL) {
      this._base = baseURL || '';
    },

    _buildURL(path) {
      if (path.startsWith('http')) return path;
      return this._base + path;
    },

    async get(url) {
      const response = await fetch(this._buildURL(url));
      if (!response.ok) {
        const text = await response.text().catch(function () { return '请求失败'; });
        throw new Error('HTTP ' + response.status + ': ' + text);
      }
      return response.json();
    },

    async post(url, data) {
      const response = await fetch(this._buildURL(url), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data)
      });
      if (!response.ok) {
        const text = await response.text().catch(function () { return '请求失败'; });
        throw new Error('HTTP ' + response.status + ': ' + text);
      }
      return response.json();
    }
  };

  /* ------------------------------------------------------------------------
     Loading State Manager
     ------------------------------------------------------------------------ */
  const Loading = {
    _states: {},

    /** Create a loading skeleton inside an element */
    skeleton(container, type) {
      type = type || 'table';
      container = typeof container === 'string' ? document.querySelector(container) : container;
      if (!container) return;

      if (type === 'table') {
        container.innerHTML =
          '<div class="p-4">' +
            Array.from({ length: 5 }, function (_, i) {
              return '<div class="flex gap-3 mb-3" style="animation-delay:' + (i * 80) + 'ms">' +
                '<div class="skeleton h-4" style="width:' + (30 + Math.random() * 30) + '%"></div>' +
                '<div class="skeleton h-4" style="width:' + (20 + Math.random() * 20) + '%"></div>' +
                '<div class="skeleton h-4" style="width:' + (25 + Math.random() * 25) + '%"></div>' +
              '</div>';
            }).join('') +
          '</div>';
      } else if (type === 'cards') {
        container.innerHTML =
          '<div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">' +
            Array.from({ length: 3 }, function (_, i) {
              return '<div style="animation-delay:' + (i * 60) + 'ms">' +
                '<div class="skeleton h-32 rounded-xl"></div>' +
              '</div>';
            }).join('') +
          '</div>';
      }
    },

    /** Set loading state on a button */
    button(btn, loading) {
      btn = typeof btn === 'string' ? document.querySelector(btn) : btn;
      if (!btn) return;

      if (loading) {
        btn._originalHTML = btn.innerHTML;
        btn._originalDisabled = btn.disabled;
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner spinner--sm"></span>' + (btn.dataset.loadingText || '处理中...');
        btn.classList.add('pointer-events-none');
      } else {
        if (btn._originalHTML !== undefined) {
          btn.innerHTML = btn._originalHTML;
          btn.disabled = btn._originalDisabled;
          btn.classList.remove('pointer-events-none');
        }
      }
    }
  };

  /* ------------------------------------------------------------------------
     Mobile Navigation
     ------------------------------------------------------------------------ */
  function initMobileNav() {
    var toggle = document.querySelector('.nav__toggle');
    var mobile = document.querySelector('.nav__mobile');
    if (!toggle || !mobile) return;

    toggle.addEventListener('click', function () {
      var isOpen = mobile.classList.contains('nav__mobile--open');
      if (isOpen) {
        mobile.classList.remove('nav__mobile--open');
        toggle.setAttribute('aria-expanded', 'false');
      } else {
        mobile.classList.add('nav__mobile--open');
        toggle.setAttribute('aria-expanded', 'true');
      }
    });

    mobile.addEventListener('click', function (e) {
      if (e.target === mobile) {
        mobile.classList.remove('nav__mobile--open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && mobile.classList.contains('nav__mobile--open')) {
        mobile.classList.remove('nav__mobile--open');
        toggle.setAttribute('aria-expanded', 'false');
        toggle.focus();
      }
    });
  }

  /* ------------------------------------------------------------------------
     Table Sorting
     ------------------------------------------------------------------------ */
  function initTableSort(tableEl, dataArray, renderFn) {
    tableEl = typeof tableEl === 'string' ? document.querySelector(tableEl) : tableEl;
    if (!tableEl) return;

    var currentSort = { key: null, dir: 'asc' };

    tableEl.addEventListener('click', function (e) {
      var th = e.target.closest('th[data-sort]');
      if (!th) return;

      var key = th.dataset.sort;
      if (currentSort.key === key) {
        currentSort.dir = currentSort.dir === 'asc' ? 'desc' : 'asc';
      } else {
        currentSort.key = key;
        currentSort.dir = 'asc';
      }

      // Update sort indicators
      tableEl.querySelectorAll('th[data-sort]').forEach(function (el) {
        el.dataset.sortDir = '';
      });
      th.dataset.sortDir = currentSort.dir;

      // Sort data
      var sorted = [].concat(dataArray).sort(function (a, b) {
        var va = a[key], vb = b[key];
        if (va == null) va = '';
        if (vb == null) vb = '';
        if (typeof va === 'number' && typeof vb === 'number') {
          return currentSort.dir === 'asc' ? va - vb : vb - va;
        }
        va = String(va).toLowerCase();
        vb = String(vb).toLowerCase();
        if (currentSort.dir === 'asc') return va < vb ? -1 : va > vb ? 1 : 0;
        return va > vb ? -1 : va < vb ? 1 : 0;
      });

      if (renderFn) renderFn(sorted);
    });
  }

  /* ------------------------------------------------------------------------
     Search / Filter
     ------------------------------------------------------------------------ */
  function createSearchFilter(inputEl, items, matchFn, renderFn, debounceMs) {
    inputEl = typeof inputEl === 'string' ? document.querySelector(inputEl) : inputEl;
    debounceMs = debounceMs || 200;

    var timer;
    inputEl.addEventListener('input', function () {
      clearTimeout(timer);
      timer = setTimeout(function () {
        var query = inputEl.value.toLowerCase().trim();
        if (!query) {
          renderFn(items);
          return;
        }
        var filtered = items.filter(function (item) { return matchFn(item, query); });
        renderFn(filtered);
      }, debounceMs);
    });
  }

  /* ------------------------------------------------------------------------
     Undo Snackbar
     ------------------------------------------------------------------------ */
  function undoToast(message, undoCallback, duration) {
    duration = duration || 6000;
    var toastObj = Toast.show(
      message + ' <button class="undo-link" style="background:none;border:none;color:#2563eb;cursor:pointer;font-weight:600;margin-left:8px;padding:0;text-decoration:underline;">撤销</button>',
      'info',
      duration
    );

    setTimeout(function () {
      var btn = document.querySelector('.undo-link');
      if (btn) {
        btn.addEventListener('click', function (e) {
          e.preventDefault();
          undoCallback();
          if (toastObj && toastObj.remove) toastObj.remove();
        });
      }
    }, 100);

    return toastObj;
  }

  /* ------------------------------------------------------------------------
     Utility Functions
     ------------------------------------------------------------------------ */
  function escapeHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function formatNumber(n) {
    if (n == null) return '-';
    return Number(n).toLocaleString('zh-CN');
  }

  function formatDate(dateStr) {
    if (!dateStr) return '-';
    try {
      var d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' }) +
        ' ' + d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    } catch (e) {
      return dateStr;
    }
  }

  function formatDateOnly(dateStr) {
    if (!dateStr) return '-';
    // If already YYYY-MM-DD, return as-is in zh-CN format
    if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
      return dateStr;
    }
    try {
      var d = new Date(dateStr);
      if (isNaN(d.getTime())) return dateStr;
      return d.toLocaleDateString('zh-CN', { year: 'numeric', month: '2-digit', day: '2-digit' });
    } catch (e) {
      return dateStr;
    }
  }

  function getStatusBadge(status) {
    if (!status) return '<span class="badge badge--neutral">暂无数据</span>';
    var s = status.toString().trim();
    // Green: active/effective/live statuses
    if (/^(生效|激活|Active|Live|Released|已发布)$/i.test(s)) {
      return '<span class="badge badge--success">' + escapeHtml(s) + '</span>';
    }
    // Red: inactive/obsolete/expired statuses
    if (/^(失效|废弃|Obsolete|Expired|Inactive|已作废)$/i.test(s)) {
      return '<span class="badge badge--danger">' + escapeHtml(s) + '</span>';
    }
    // Orange: pending/review/frozen
    if (/^(待审核|冻结|Pending|Frozen|Under Review|审核中)$/i.test(s)) {
      return '<span class="badge badge--warning">' + escapeHtml(s) + '</span>';
    }
    // Blue: other known statuses
    return '<span class="badge badge--info">' + escapeHtml(s) + '</span>';
  }

  function debounce(fn, delay) {
    var timer;
    return function () {
      var ctx = this, args = arguments;
      clearTimeout(timer);
      timer = setTimeout(function () { fn.apply(ctx, args); }, delay);
    };
  }

  /** Simple client-side pagination */
  function paginate(items, page, pageSize) {
    page = page || 1;
    pageSize = pageSize || 20;
    var totalPages = Math.ceil(items.length / pageSize);
    var start = (page - 1) * pageSize;
    return {
      items: items.slice(start, start + pageSize),
      page: page,
      pageSize: pageSize,
      totalPages: totalPages,
      total: items.length,
      hasPrev: page > 1,
      hasNext: page < totalPages
    };
  }

  /** Render pagination controls */
  function renderPagination(pager, container, onPageChange) {
    container = typeof container === 'string' ? document.querySelector(container) : container;
    if (!container || pager.totalPages <= 1) {
      if (container) container.innerHTML = '';
      return;
    }

    var html = '<div class="flex items-center justify-between pt-3 text-sm">';
    html += '<span class="text-gray-500">共 ' + pager.total + ' 条，第 ' + pager.page + '/' + pager.totalPages + ' 页</span>';
    html += '<div class="flex gap-1">';

    html += '<button class="btn btn--ghost btn--sm" ' + (pager.hasPrev ? '' : 'disabled') +
      ' data-page="' + (pager.page - 1) + '">上一页</button>';

    // Page numbers
    var start = Math.max(1, pager.page - 2);
    var end = Math.min(pager.totalPages, pager.page + 2);
    if (start > 1) html += '<button class="btn btn--ghost btn--sm" data-page="1">1</button>';
    if (start > 2) html += '<span class="px-1 text-gray-400">...</span>';
    for (var i = start; i <= end; i++) {
      html += '<button class="btn btn--sm ' + (i === pager.page ? 'btn--primary' : 'btn--ghost') +
        '" data-page="' + i + '">' + i + '</button>';
    }
    if (end < pager.totalPages - 1) html += '<span class="px-1 text-gray-400">...</span>';
    if (end < pager.totalPages) html += '<button class="btn btn--ghost btn--sm" data-page="' + pager.totalPages + '">' + pager.totalPages + '</button>';

    html += '<button class="btn btn--ghost btn--sm" ' + (pager.hasNext ? '' : 'disabled') +
      ' data-page="' + (pager.page + 1) + '">下一页</button>';

    html += '</div></div>';
    container.innerHTML = html;

    container.querySelectorAll('button[data-page]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var p = parseInt(this.dataset.page);
        if (p && onPageChange) onPageChange(p);
      });
    });
  }

  /* ------------------------------------------------------------------------
     Export to Global
     ------------------------------------------------------------------------ */
  global.BOMApp = {
    Toast: Toast,
    Modal: Modal,
    API: API,
    Loading: Loading,
    initMobileNav: initMobileNav,
    initTableSort: initTableSort,
    createSearchFilter: createSearchFilter,
    undoToast: undoToast,
    escapeHtml: escapeHtml,
    formatNumber: formatNumber,
    formatDate: formatDate,
    formatDateOnly: formatDateOnly,
    getStatusBadge: getStatusBadge,
    debounce: debounce,
    paginate: paginate,
    renderPagination: renderPagination,
    ready: ready
  };

  // Auto-init mobile nav on DOM ready
  ready(initMobileNav);

})(window);
