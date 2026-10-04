"""Fabriques communes aux tests analyst_* : bloc de fichier balisé, bloc de modification, fichier livré."""


from analyst_blocks import parse_file_sections


def parse_file_blocks(text):
    return parse_file_sections(text)[0]


def block(path, content, lang="ts"):
    return f"<<<FICHIER: {path}>>>\n```{lang}\n{content}```\n<<<FIN_FICHIER>>>\n"


def edit(path, *pairs):
    body = "".join(f"<<<<<<< CHERCHER\n{a}\n=======\n{b}\n>>>>>>> REMPLACER\n" for a, b in pairs)
    return f"<<<MODIFICATION: {path}>>>\n{body}<<<FIN_MODIFICATION>>>\n"


def f(path, content):
    return {"path": path, "content": content}
