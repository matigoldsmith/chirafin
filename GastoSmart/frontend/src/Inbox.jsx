import React, { useState, useEffect, useMemo } from 'react';
import { 
  Check, X, Activity, Archive, Clock, Eye, Trash2, HelpCircle,
  Calendar, CreditCard, Store, Tag, ChevronRight, LayoutGrid, List, Search, Filter, CheckSquare, Square, MoreHorizontal, AlertCircle
} from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const API_URL = 'http://localhost:8000';

const Inbox = () => {
  const [view, setView] = useState('pending');
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedItem, setSelectedItem] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [filterCategory, setFilterCategory] = useState('Todas');
  const [selectedIds, setSelectedIds] = useState([]);
  const [notification, setNotification] = useState(null);
  const [layout, setLayout] = useState('grid');
  const [displayCount, setDisplayCount] = useState(40);

  const categories = ['Supermercado', 'Restaurante', 'Transporte', 'Farmacia', 'Servicios', 'Hogar', 'Tecnología', 'Salud', 'Hobby', 'Otros'];

  const showNotification = (message, type = 'success') => {
    setNotification({ message, type });
    setTimeout(() => setNotification(null), 3000);
  };

  const formatDate = (dateStr) => {
    if (!dateStr) return '';
    try {
      const parts = dateStr.split('-');
      const date = parts.length === 3 ? new Date(parts[0], parts[1] - 1, parts[2]) : new Date(dateStr);
      
      const day = date.getDate();
      const monthNames = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"
      ];
      const month = monthNames[date.getMonth()];
      const year = date.getFullYear();
      
      return `${day} ${month} ${year}`;
    } catch (e) {
      return dateStr;
    }
  };

  const fetchItems = async (currentView) => {
    try {
      const endpoint = currentView === 'pending' ? '/pending' : 
                      currentView === 'ignored' ? '/ignored' : 
                      currentView === 'history' ? '/history' : '/all';
      
      const res = await fetch(`${API_URL}${endpoint}`);
      if (!res.ok) throw new Error("Error de conexión");
      const data = await res.json();
      setItems(data);
      setError(null);
      setSyncing(true);
      setTimeout(() => setSyncing(false), 2000);
    } catch (err) {
      setError("Fallo de conexión con el motor GastoSmart.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchItems(view);
    const interval = setInterval(() => fetchItems(view), 8000);
    return () => clearInterval(interval);
  }, [view]);

  const handleAction = async (id, updatedData, newState) => {
    try {
      const res = await fetch(`${API_URL}/confirm/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({...updatedData, estado: newState})
      });
      if (res.ok) {
        setSelectedItem(null);
        showNotification(newState === 'Confirmado' ? 'Gasto confirmado' : 'Movido a Ignorados');
        fetchItems(view);
      }
    } catch (err) {
      showNotification("Error al guardar cambios", "error");
    }
  };

  const handleMassAction = async (newState) => {
    const promises = selectedIds.map(id => {
      const item = items.find(i => i.id === id);
      return fetch(`${API_URL}/confirm/${id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({...item, estado: newState})
      });
    });
    
    try {
      await Promise.all(promises);
      setSelectedIds([]);
      showNotification(`${selectedIds.length} actualizados`);
      fetchItems(view);
    } catch (err) {
      showNotification("Error masivo", "error");
    }
  };

  const toggleSelect = (e, id) => {
    e.stopPropagation();
    setSelectedIds(prev => 
      prev.includes(id) ? prev.filter(i => i !== id) : [...prev, id]
    );
  };

  const selectAll = () => {
    if (selectedIds.length === filteredItems.length) {
      setSelectedIds([]);
    } else {
      setSelectedIds(filteredItems.map(i => i.id));
    }
  };

  const filteredItems = useMemo(() => {
    return items.filter(item => {
      const matchesSearch = (item.comercio || '').toLowerCase().includes(searchTerm.toLowerCase()) ||
                            (item.categoria || '').toLowerCase().includes(searchTerm.toLowerCase());
      const matchesFilter = filterCategory === 'Todas' || item.categoria === filterCategory;
      return matchesSearch && matchesFilter;
    });
  }, [items, searchTerm, filterCategory]);

  return (
    <div className="app-container">
      <nav className="header-glass">
        <div className="brand-elite">
          <div className="brand-icon">G</div>
          <span>GastoSmart Elite</span>
        </div>

        <div className="nav-pill-box">
          <button className={`nav-pill ${view === 'pending' ? 'active' : ''}`} onClick={() => setView('pending')}>Nuevos</button>
          <button className={`nav-pill ${view === 'history' ? 'active' : ''}`} onClick={() => setView('history')}>Historial</button>
          <button className={`nav-pill ${view === 'ignored' ? 'active' : ''}`} onClick={() => setView('ignored')}>Ignorados</button>
          <button className={`nav-pill ${view === 'all' ? 'active' : ''}`} onClick={() => setView('all')}>Database</button>
        </div>

        <div style={{display: 'flex', alignItems: 'center', gap: '1rem'}}>
           <div className={`status-badge ${syncing ? 'syncing' : ''}`}>
             <div className="dot"></div>
             {syncing ? 'Sincronizando' : 'Al día'}
           </div>
        </div>
      </nav>

      <main className="main-stage">
        <header className="hero-section">
          <h1>{view === 'pending' ? 'Bandeja de Entrada' : (view === 'history' ? 'Mi Historial' : (view === 'ignored' ? 'Archivo' : 'Catálogo Completo'))}</h1>
          <p>Potenciado por inteligencia artificial para un control total.</p>
        </header>

        <section className="elite-toolbar">
          <div className="search-field">
            <Search className="icon" size={20} />
            <input 
              placeholder="Buscar por comercio, categoría..." 
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
          
          <select className="filter-select" value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)}>
            <option value="Todas">Todas las categorías</option>
            {categories.map(c => <option key={c} value={c}>{c}</option>)}
          </select>

          <div style={{display: 'flex', gap: '4px', background: '#f2f2f7', padding: '4px', borderRadius: '12px'}}>
             <button className={`nav-pill ${layout === 'grid' ? 'active' : ''}`} onClick={() => setLayout('grid')}><LayoutGrid size={18}/></button>
             <button className={`nav-pill ${layout === 'table' ? 'active' : ''}`} onClick={() => setLayout('table')}><List size={18}/></button>
          </div>

          <button className="btn-elite secondary" onClick={selectAll}>
            {selectedIds.length === filteredItems.length && filteredItems.length > 0 ? 'Desmarcar' : 'Check Todo'}
          </button>
        </section>

        {loading && items.length === 0 ? (
          <div style={{display: 'flex', justifyContent: 'center', padding: '10vh'}}>
             <Activity className="animate-spin" size={42} color="var(--premium-accent)" />
          </div>
        ) : error ? (
           <div style={{textAlign: 'center', padding: '4rem', background: '#fff1f0', borderRadius: '32px'}}>
             <AlertCircle size={48} color="var(--premium-danger)" style={{marginBottom: '1rem'}}/>
             <h2 style={{fontWeight: 800}}>Fallo de Motor</h2>
             <p>{error}</p>
           </div>
        ) : (
          <>
            {layout === 'grid' ? (
              <div className="elite-grid">
                {filteredItems.slice(0, displayCount).map(item => (
                  <div 
                    key={item.id} 
                    className={`elite-card ${selectedIds.includes(item.id) ? 'selected' : ''}`}
                    onClick={() => setSelectedItem(item)}
                  >
                    <div 
                      className={`selection-indicator ${selectedIds.includes(item.id) ? 'selected' : ''}`} 
                      onClick={(e) => toggleSelect(e, item.id)}
                    >
                       <Check size={16} />
                    </div>
                    
                    <div className="card-visual">
                       <img 
                         src={`${API_URL}/receipt/${item.foto_path.split('/').pop()}`} 
                         loading="lazy"
                         onError={(e) => e.target.src = 'https://placehold.co/600x400/f8f9fa/cbd5e1?text=SIN+FOTO'} 
                       />
                       <div className="quick-hover-actions">
                          <button className="action-btn-circle confirm" onClick={(e) => { e.stopPropagation(); handleAction(item.id, item, 'Confirmado'); }}>
                            <Check size={24} />
                          </button>
                          <button className="action-btn-circle reject" onClick={(e) => { e.stopPropagation(); handleAction(item.id, item, 'Ignorado'); }}>
                            <X size={24} />
                          </button>
                       </div>
                    </div>

                    <div className="card-info-elite">
                       <div className="card-top">
                          <div className="merchant-name" style={{opacity: item.comercio ? 1 : 0.2}}>{item.comercio || 'Sin Nombre'}</div>
                          <div className="expense-date">{formatDate(item.fecha)}</div>
                       </div>
                       <div className="card-mid">
                          <span className="premium-tag">{item.categoria || 'Otros'}</span>
                       </div>
                       <div className="card-price">
                          <span className="currency">{item.moneda}</span>
                          {Math.round(item.monto || 0).toLocaleString()}
                       </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="elite-table-container" style={{background: 'white', borderRadius: '24px', padding: '1rem', border: '1px solid var(--premium-border)'}}>
                <table style={{width: '100%', borderCollapse: 'collapse'}}>
                   <thead>
                      <tr style={{textAlign: 'left', borderBottom: '1px solid var(--premium-border)'}}>
                         <th style={{padding: '1rem'}}>Nombre</th>
                         <th>Categoría</th>
                         <th>Fecha</th>
                         <th style={{textAlign: 'right', paddingRight: '1rem'}}>Monto</th>
                      </tr>
                   </thead>
                   <tbody>
                      {filteredItems.slice(0, displayCount).map(item => (
                         <tr 
                          key={item.id} 
                          onClick={() => setSelectedItem(item)}
                          style={{borderBottom: '1px solid var(--premium-border)', cursor: 'pointer'}}
                          onMouseEnter={(e) => e.currentTarget.style.background = '#f9f9fb'}
                          onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                         >
                            <td style={{padding: '1rem', fontWeight: 600}}>{item.comercio || 'Sin Nombre'}</td>
                            <td><span className="premium-tag">{item.categoria}</span></td>
                            <td style={{color: 'var(--premium-text-muted)'}}>{formatDate(item.fecha)}</td>
                            <td style={{textAlign: 'right', paddingRight: '1rem', fontWeight: 700, color: 'var(--premium-accent)'}}>{item.moneda} {Math.round(item.monto || 0).toLocaleString()}</td>
                         </tr>
                      ))}
                   </tbody>
                </table>
              </div>
            )}
            
            {filteredItems.length > displayCount && (
              <div style={{textAlign: 'center', marginTop: '3rem'}}>
                 <button className="btn-elite secondary" onClick={() => setDisplayCount(prev => prev + 40)}>
                   Ver más ({filteredItems.length - displayCount} restantes)
                 </button>
              </div>
            )}
          </>
        )}
      </main>

      <AnimatePresence>
        {selectedItem && (
          <div className="modal-blur-overlay" onClick={() => setSelectedItem(null)}>
            <motion.div 
               initial={{opacity: 0, scale: 0.95, y: 30}}
               animate={{opacity: 1, scale: 1, y: 0}}
               exit={{opacity: 0, scale: 0.95, y: 30}}
               className="elite-modal" onClick={e => e.stopPropagation()}
            >
              <div className="modal-canvas">
                 <img src={`${API_URL}/receipt/${selectedItem.foto_path.split('/').pop()}`} />
              </div>
              <div className="modal-form-area">
                 <h2>Detalles del Gasto</h2>
                 <p style={{color: 'var(--premium-text-muted)', marginBottom: '3rem'}}>Verifica y confirma para tu base de datos.</p>

                 <div className="form-item">
                    <span className="premium-label">Establecimiento</span>
                    <input className="premium-input" value={selectedItem.comercio} onChange={e => setSelectedItem({...selectedItem, comercio: e.target.value})} />
                 </div>

                 <div style={{display: 'flex', gap: '2rem'}}>
                    <div style={{flex: 1}}>
                       <span className="premium-label">Fecha</span>
                       <input type="date" className="premium-input" value={selectedItem.fecha} onChange={e => setSelectedItem({...selectedItem, fecha: e.target.value})} />
                    </div>
                    <div style={{flex: 1}}>
                       <span className="premium-label">Categoría</span>
                       <input list="cats" className="premium-input" value={selectedItem.categoria} onChange={e => setSelectedItem({...selectedItem, categoria: e.target.value})} />
                       <datalist id="cats">
                          {categories.map(c => <option key={c} value={c}/>)}
                       </datalist>
                    </div>
                 </div>

                 <div style={{display: 'flex', gap: '2rem'}}>
                    <div style={{flex: 0.4}}>
                       <span className="premium-label">Divisa</span>
                       <input className="premium-input" value={selectedItem.moneda} onChange={e => setSelectedItem({...selectedItem, moneda: e.target.value})} />
                    </div>
                    <div style={{flex: 1}}>
                       <span className="premium-label">Monto</span>
                       <input type="number" className="premium-input" value={selectedItem.monto} onChange={e => setSelectedItem({...selectedItem, monto: e.target.value})} />
                    </div>
                 </div>

                 <div className="modal-elite-footer">
                    <button className="btn-elite secondary" onClick={() => setSelectedItem(null)}>Cerrar</button>
                    <button className="btn-elite danger" onClick={() => handleAction(selectedItem.id, selectedItem, 'Ignorado')}>
                       <Trash2 size={20} /> Descartar
                    </button>
                    <button className="btn-elite primary" onClick={() => handleAction(selectedItem.id, selectedItem, 'Confirmado')}>
                       <Check size={20} /> Confirmar
                    </button>
                 </div>
              </div>
            </motion.div>
          </div>
        )}

        {notification && (
          <motion.div 
            initial={{opacity: 0, y: 20}} animate={{opacity: 1, y: 0}} exit={{opacity: 0}}
            style={{
              position: 'fixed', bottom: '100px', right: '40px', 
              background: notification.type === 'error' ? 'var(--premium-danger)' : '#1d1d1f',
              color: 'white', padding: '16px 24px', borderRadius: '16px', zIndex: 9999,
              fontWeight: 700, display: 'flex', alignItems: 'center', gap: '12px', boxShadow: '0 20px 40px rgba(0,0,0,0.2)'
            }}
          >
            <Check size={20}/> {notification.message}
          </motion.div>
        )}

        {selectedIds.length > 0 && (
          <motion.div initial={{y: 100, x: '-50%'}} animate={{y: 0, x: '-50%'}} className="mass-floating-bar">
             <div className="selection-count">{selectedIds.length} seleccionados</div>
             <div className="mass-actions-group">
                <button className="mass-action-btn" onClick={() => handleMassAction('Confirmado')}><Check size={18} /> Confirmar</button>
                <button className="mass-action-btn danger" onClick={() => handleMassAction('Ignorado')}><Trash2 size={18} /> Descartar</button>
                <button className="mass-action-btn" onClick={() => setSelectedIds([])}>Cancelar</button>
             </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default Inbox;
