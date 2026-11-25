"""Native Qt rich text for the semantic Markdown view, without a WebView."""
from PySide6 import QtCore as C, QtGui as G, QtWidgets as W
from .document_composition import parser
import html


class MarkdownView(W.QTextBrowser):
    def __init__(self,value,store,parent=None,*,auto_height=True,strict=True,math_size=16):
        super().__init__(parent);self.setFrameShape(W.QFrame.NoFrame)
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.store=store;self.auto_height=auto_height;self.strict=strict;self.math_size=math_size
        self.setMinimumHeight(160);self.setSizePolicy(W.QSizePolicy.Expanding,W.QSizePolicy.Preferred)
        self.anchorClicked.connect(lambda url:G.QDesktopServices.openUrl(url) if url.scheme() in ('https','http','mailto') else None)
        self.document().documentLayout().documentSizeChanged.connect(self.fit_height)
        self.set_markdown(value)

    def set_markdown(self,value):
        store=self.store
        md=parser();resources=[]
        self.setStyleSheet("QTextBrowser { selection-background-color: #c6e4df; }")
        def math_render(renderer,tokens,idx,options,env):
            from .math_display import raster
            try:
                size, image = raster(tokens[idx].content, {1:27,2:19,3:15}.get(env.get('_heading_level'),self.math_size), max(2,self.devicePixelRatioF()))
            except Exception as exc:
                if self.strict:raise
                return '<code title="这条公式的语法需要核对">'+html.escape(tokens[idx].content)+'</code>'
            name='math-'+str(len(resources));resources.append((name,image))
            return '<img src="'+name+'" width="'+str(size.width())+'" height="'+str(size.height())+'" alt="'+html.escape(tokens[idx].content,quote=True)+'">'
        def image_render(renderer,tokens,idx,options,env):
            source=tokens[idx].attrGet('src') or ''
            if not source.startswith('anchor:'):
                # Native chat never fetches remote/local Markdown image paths.
                # Keep the actual description/address visible for the reader.
                return html.escape(tokens[idx].content+' ['+source+']')
            identity=source[7:];anchor=store.anchor(identity)
            page=store.one('SELECT image FROM pages WHERE source_id=? AND number=?',(anchor['source_id'],anchor['page']))
            image=G.QImage(str(store.asset_path(page['image'])))
            if anchor.get('region'):
                x0,y0,x1,y1=anchor['region'];image=image.copy(round(x0*image.width()),round(y0*image.height()),round((x1-x0)*image.width()),round((y1-y0)*image.height()))
            if image.width()>720:image=image.scaledToWidth(720,C.Qt.SmoothTransformation)
            name='image-'+str(len(resources));resources.append((name,image))
            return '<img src="'+name+'">'
        for kind in ('math_inline','math_block','math_block_label','math_block_eqno'):md.add_render_rule(kind,math_render)
        def prose(renderer,tokens,idx,options,env):
            from .reading_design import prose_pieces
            from markdown_it.token import Token
            parts=[]
            for mathematical,part in prose_pieces(tokens[idx].content,joined=True):
                if mathematical:
                    token=Token('math_inline','math',0);token.content=part
                    parts.append(math_render(renderer,[token],0,options,env))
                else:parts.append(html.escape(part))
            return ''.join(parts)
        md.add_render_rule('text',prose)
        def heading_open(renderer,tokens,idx,options,env):
            env['_heading_level']=int(tokens[idx].tag[1:])
            return renderer.renderToken(tokens,idx,options,env)
        def heading_close(renderer,tokens,idx,options,env):
            env.pop('_heading_level',None)
            return renderer.renderToken(tokens,idx,options,env)
        md.add_render_rule('heading_open',heading_open);md.add_render_rule('heading_close',heading_close)
        md.add_render_rule('image',image_render)
        from .source_math import resolve_quote
        body=md.render(value, {"source_formula":lambda quote, href:resolve_quote(store, quote, href)})
        self.document().clear()
        self.document().setDefaultStyleSheet('a {color:#28656d;} p {margin: 0 0 8px;} h1 {font-size:27px;} h2 {font-size:19px;} h3 {font-size:15px;} table {border-collapse:collapse;} td,th {border:1px solid #d9d9d9;padding:7px;} th {background-color:#e8eef1;}')
        for name,image in resources:self.document().addResource(G.QTextDocument.ImageResource,C.QUrl(name),image)
        self.setHtml(body)
        self.fit_height()
    def fit_height(self,*args):
        if not self.auto_height:return
        self.document().setTextWidth(max(100,self.viewport().width()))
        height=round(self.document().size().height())+12
        if abs(self.minimumHeight()-height)>2 or self.maximumHeight()!=self.minimumHeight():self.setFixedHeight(height)
    def resizeEvent(self,event):
        super().resizeEvent(event);self.fit_height()
