import React from 'react';

const Sidebar = ({ pages, activePage, setActivePage }) => {
  return (
    <aside className="sidebar">
      <div className="sidebar-title">debot</div>
      <nav className="sidebar-nav">
        <ul>
          {pages.map((p) => (
            <li
              key={p.name}
              className={activePage === p.name ? 'active' : ''}
              onClick={() => setActivePage(p.name)}
            >
              <span className="sidebar-icon">{p.icon}</span> {p.name}
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
};

export default Sidebar;
