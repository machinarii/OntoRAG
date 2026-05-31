#!/bin/bash

NAMESPACE=rag
helm uninstall ontorag-dev --namespace $NAMESPACE
