#!/usr/bin/env python3

import tornado.ioloop
import tornado.web
import os.path
import sys
import tempfile
import subprocess
import re

from bs4 import BeautifulSoup

from argparse import ArgumentParser

class Messenger:
    def __init__(self):
        self.items = []
    def queue(self, msg):
        self.items.append(msg)

messenger = Messenger()

def is_hidden(filename):
    name = os.path.basename(os.path.abspath(filename))
    return name.startswith('.')

def is_asciidoc(filename):
    ext = os.path.splitext(filename)[1]
    return ext == '.adoc'

def check_valid_filename(filename):
    return not is_hidden(filename) and is_asciidoc(filename)

class ProcessHandler:
    def __init__(self, args):
        process = subprocess.run(args, text=True)
        self.stdout = process.stdout
        self.stderr = process.stderr
        self.returncode = process.returncode

def get_html_element(name, source):
    soup = BeautifulSoup(source, 'html.parser')
    body = soup.find(name)
    if body is None:
        return None
    content = ''
    for item in body.contents:
        content += str(item)
    return content

class LinkFixer:
    def __init__(self, html):
        self.html = html
    def fix_all_links(self):
        soup = BeautifulSoup(self.html, 'html.parser')
        for a in soup.find_all('a'):
            link = a['href']
            if re.match('http:[a-z]', link) is not None:
                link = link[link.find(':')+1:]
            # if no protocol was specified by user, assume just http
            if re.match('http[s|]', link) is None:
                link = 'http://' + link
            a['href'] = link
        self.html = str(soup)

class PageRenderer:
    def __init__(self, filename):
        self.filename = filename
        self.html = None
    def render(self):
        tmpdir = tempfile.TemporaryDirectory(dir='/tmp/')
        args = ['asciidoc', '-o', os.path.join(tmpdir.name, 'out.html'), self.filename]
        handler = ProcessHandler(args)
        self.html = None
        with open(os.path.join(tmpdir.name, 'out.html')) as f:
            self.html = f.read()
        link_fixer = LinkFixer(self.html)
        link_fixer.fix_all_links()
        self.html = link_fixer.html
        tmpdir.cleanup()

def remove_root_path(root, path):
    result = ''
    root_items = root.split(os.sep)
    path_items = path.split(os.sep)

    pathIsAbs = False
    if root_items[0] == '':
        root_items = root_items[1:]
    if path_items[0] == '':
        pathIsAbs = True
        path_items = path_items[1:]
    trigger = False
    while path_items[0] == root_items[0]:
        if len(root_items) == 1:
            trigger = True
        path_items = path_items[1:]
        root_items = root_items[1:]
        if trigger:
            break
    return os.path.join(*path_items)   
    
class InternalError(BaseException):
    def __init__(self, msg):
        self.msg = msg
    def __str__(self):
        return self.msg

class FileFinder:
    def __init__(self, dir):
        self.dir = dir
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
    def search(self, searchterm, case=True):
        l = []
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
        for root, dirs, files in os.walk(self.dir):
            for name in files:
                if not check_valid_filename(name):
                    continue
                search_term = remove_root_path(self.dir, os.path.join(root, name))
                if case:
                    if not search_term.find(searchterm) == -1:
                        l.append(os.path.join(root, name))
                else:
                    if not search_term.lower().find(searchterm.lower()) == -1:
                        l.append(os.path.join(root, name))
        return l

class FileContentFinder:
    def __init__(self, dir):
        self.dir = dir
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
    def search(self, term, case=True):
        l = []
        if not case:
            term = term.lower()
        if not os.path.isdir(self.dir):
            raise InternalError('{} is not a directory'.format(self.dir))
        for root, dirs, files in os.walk(self.dir):
            for name in files:
                if not check_valid_filename(name):
                    continue
                with open(os.path.join(root, name), 'r') as f:
                    content = f.read()
                    if not case:
                        content = content.lower()
                    if content.find(term) != -1:
                        l.append(os.path.join(root, name))
        return l

class BaseHandler(tornado.web.RequestHandler):
    def initialize(self, options):
        self.options = options
    def special_render(self, __name, **options):
        self.render(__name, name=self.options['name'], messages=messenger.items, base=self.options['base'], **options)

class RootHandler(BaseHandler):
    def get(self):
        self.special_render('base.template.html')

class SearchHandler(BaseHandler):
    def get_search_results(self, term):
        files1 = FileFinder(self.options['source']).search(term, case=False)
        files2 = FileContentFinder(self.options['source']).search(term, case=False)
        files1 = [remove_root_path(self.options['source'], f) for f in files1]
        files2 = [remove_root_path(self.options['source'], f) for f in files2]
        nodups = list(set(files1 + files2))
        noinvalid = filter(lambda x: check_valid_filename(x), nodups)
        return noinvalid
    def get(self):
        self.redirect('/')
    def post(self):
        self.special_render('search.template.html', results=self.get_search_results(self.get_argument('search')))

class PageHandler(BaseHandler):
    def get(self):
        name = self.get_argument('name')
        if name is None or not check_valid_filename(name):
            self.redirect('/')
        name = os.path.join(self.options['source'], name)
        renderer = PageRenderer(name)
        renderer.render()
        body_text = get_html_element('body', renderer.html)
        head_text = get_html_element('head', renderer.html)
        self.special_render('page.template.html', page_head=head_text, page_body=body_text)

class TreeHandler(BaseHandler):
    def get(self):
        l = []
        for root, dirs, files in os.walk(self.options['source']):
            for name in files:
                l.append(remove_root_path(self.options['source'], os.path.join(root, name)))
        l = filter(lambda x: check_valid_filename(x), l)
        self.special_render('tree.template.html', tree=l)

def create_options(source="/doc", name="AsciiDoc Viewer", home_page='home.adoc', base_dir='/'):
    return dict(source=source, name=name, home_page=home_page, base=base_dir)

base_dir = os.path.dirname(os.path.abspath(__file__))

def create_app(options):
    app = tornado.web.Application([
        (r"/", RootHandler, dict(options=options)),
        (r"/page", PageHandler, dict(options=options)),
        (r"/search", SearchHandler, dict(options=options)),
        (r"/tree", TreeHandler, dict(options=options)),
        # (r"/options", OptionsHandler, dict(options=options)),
    ], template_path=os.path.join(base_dir, 'templates'), static_path=os.path.join(base_dir, 'static'), xheaders=True)
    return app

def main():
    parser = ArgumentParser()
    parser.add_argument('--port', type=int, help="Port to listen on", default=2959)
    parser.add_argument('--source', type=str, help="Directory containing source asciidoc", default=".")
    parser.add_argument('--name', type=str, help="Name", default="AsciiDoc Viewer")
    parser.add_argument('--base', type=str, help="Base Path",  default="")
    args = parser.parse_args()
    options = create_options(source=args.source, name=args.name, base_dir=args.base)
    app = create_app(options)
    app.listen(args.port)
    try:
        tornado.ioloop.IOLoop.current().start()
    except InternalError as e:
        print(e)
        sys.exit(1)

if __name__ == '__main__':
    main()
