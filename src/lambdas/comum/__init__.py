"""Código compartilhado por todas as funções Lambda do sebo.

O script de deploy copia este pacote para dentro do .zip de cada função,
então qualquer Lambda pode fazer `from comum.http import resposta`.
"""
